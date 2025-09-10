from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import asyncio
import json
import logging
import threading
import os
import time
from enum import Enum
from galbot_websocket_client import GalbotWebSocketClient

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Robot state management
class RobotState(Enum):
    IDLE = "idle"
    OPERATING = "operating"
    FAILED = "failed"
    SUCCEEDED = "succeeded"

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'galbot_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
galbot_client = None
available_items = []
current_tasks = {}
custom_names = {}  # Store custom display names
CUSTOM_NAMES_FILE = 'custom_names.json'

# Robot state tracking
robot_state = RobotState.IDLE
robot_state_timestamp = time.time()
robot_state_lock = threading.Lock()
state_timeout_timer = None
STATE_TIMEOUT_SECONDS = 120  # 2 minutes - give robot enough time to complete operations

def load_custom_names():
    """Load custom display names from file"""
    global custom_names
    try:
        if os.path.exists(CUSTOM_NAMES_FILE):
            with open(CUSTOM_NAMES_FILE, 'r', encoding='utf-8') as f:
                custom_names = json.load(f)
            logger.info(f"Loaded {len(custom_names)} custom names")
        else:
            custom_names = {}
    except Exception as e:
        logger.error(f"Error loading custom names: {e}")
        custom_names = {}

def save_custom_names():
    """Save custom display names to file"""
    try:
        with open(CUSTOM_NAMES_FILE, 'w', encoding='utf-8') as f:
            json.dump(custom_names, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(custom_names)} custom names")
    except Exception as e:
        logger.error(f"Error saving custom names: {e}")

def get_display_name(item):
    """Get the display name for an item (custom name if set, otherwise original)"""
    sku_id = str(item.get('sku_id', item.get('id', '')))
    return custom_names.get(sku_id, item.get('nickname', item.get('name', item.get('title', 'Unnamed Item'))))

def set_robot_state(new_state):
    """Set the robot state with thread safety and timeout management"""
    global robot_state, robot_state_timestamp, state_timeout_timer
    
    with robot_state_lock:
        old_state = robot_state
        robot_state = new_state
        robot_state_timestamp = time.time()
            
        # Cancel existing timeout timer
        if state_timeout_timer:
            state_timeout_timer.cancel()
            state_timeout_timer = None
            
        # Set timeout timer for OPERATING state
        if new_state == RobotState.OPERATING:
            state_timeout_timer = threading.Timer(STATE_TIMEOUT_SECONDS, timeout_robot_state)
            state_timeout_timer.start()
            logger.info(f"🤖 State: {old_state.value} → {new_state.value} (Timeout: {STATE_TIMEOUT_SECONDS}s)")
        else:
            logger.info(f"🤖 State: {old_state.value} → {new_state.value}")
            
        # Emit state change to ALL connected clients
        try:
            state_data = {
                'state': new_state.value,
                'timestamp': robot_state_timestamp,
                'uptime_seconds': 0
            }
            socketio.emit('robot_state_changed', state_data)
            logger.info(f"Broadcasted robot state change to all clients: {new_state.value}")
        except Exception as e:
            logger.error(f"Error emitting robot state change: {e}")

def timeout_robot_state():
    """Called when the state timeout expires"""
    global robot_state
    with robot_state_lock:
        if robot_state == RobotState.OPERATING:
            logger.warning(f"⏰ Robot state timeout after {STATE_TIMEOUT_SECONDS}s - resetting to IDLE")
            set_robot_state(RobotState.IDLE)
            
            # Emit timeout notification to frontend
            socketio.emit('robot_timeout', {
                'message': f'Robot operation timed out after {STATE_TIMEOUT_SECONDS//60} minutes'
            })

def get_robot_state():
    """Get current robot state with thread safety"""
    with robot_state_lock:
        return {
            'state': robot_state.value,
            'timestamp': robot_state_timestamp,
            'uptime_seconds': time.time() - robot_state_timestamp
        }

class AutoDroidWebApp:
    def __init__(self):
        self.client = GalbotWebSocketClient()
        self.is_connected = False
        self.loop = None
        self.thread = None
        self.should_stop = False
        self.items_loaded = False  # Flag to track if items have been loaded once
        
    async def connect_and_listen(self):
        """Connect to Galbot WebSocket and start listening with aggressive reconnection"""
        reconnect_delay = 1  # Start with 1 second delay
        max_reconnect_delay = 30  # Maximum 30 seconds between reconnection attempts
        
        while not self.should_stop:
            connection_start_time = time.time()
            
            try:
                logger.info(f"🔄 Attempting to connect to Galbot WebSocket at {self.client.websocket_url}")
                
                if await self.client.connect():
                    self.is_connected = True
                    reconnect_delay = 1  # Reset delay on successful connection
                    logger.info("✅ Connected to Galbot WebSocket with health monitoring")
                    
                    # Emit connection status to frontend
                    socketio.emit('connection_status', {'connected': True})
                    
                    # Get initial items list only on first connection
                    if not self.items_loaded:
                        await self.fetch_items_list()
                        self.items_loaded = True
                        logger.info("Items loaded on initial connection")
                    else:
                        logger.info("Reconnected - skipping item reload (items already loaded)")
                    
                    # Start listening for messages with detailed debugging
                    logger.info("🔍 DEBUG: Starting message listening loop")
                    message_count = 0
                    
                    while self.is_connected and not self.should_stop:
                        try:
                            logger.debug(f"🔍 DEBUG: Loop iteration {message_count + 1}")
                            
                            # Check connection health before receiving (compatible with different websockets versions)
                            if not self.client.websocket:
                                logger.warning("❌ DEBUG: WebSocket connection is None - breaking listen loop")
                                break
                            
                            # Check if connection is closed (handle different websockets versions)
                            try:
                                is_closed = getattr(self.client.websocket, 'closed', False)
                                logger.debug(f"🔍 DEBUG: Connection closed check: {is_closed}")
                                if is_closed:
                                    logger.warning("❌ DEBUG: WebSocket connection is closed - breaking listen loop")
                                    break
                            except Exception as e:
                                logger.debug(f"🔍 DEBUG: Could not check closed status: {e}")
                                # If we can't check closed status, continue and let receive handle it
                                pass
                            
                            logger.debug("🔍 DEBUG: About to call receive_message()")
                            message = await self.client.receive_message()
                            message_count += 1
                            logger.debug(f"🔍 DEBUG: Received message #{message_count}")
                            
                            logger.debug("🔍 DEBUG: About to handle message")
                            await self.handle_galbot_message(message)
                            logger.debug("🔍 DEBUG: Message handled successfully")
                            
                        # No timeout handling needed since we removed recv() timeout
                        except Exception as e:
                            logger.error(f"❌ DEBUG: Error in message loop: {type(e).__name__}: {e}")
                            import traceback
                            logger.error(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
                            break
                            
                    logger.info("Message listening loop ended")
                else:
                    logger.error("❌ Failed to connect to Galbot WebSocket")
                    
            except Exception as e:
                logger.error(f"Error in connect_and_listen: {e}")
            finally:
                # Always clean up connection state
                self.is_connected = False
                socketio.emit('connection_status', {'connected': False})
                
                # Close the connection properly
                try:
                    await self.client.disconnect()
                except Exception as e:
                    logger.error(f"Error during disconnect cleanup: {e}")
                
            # Calculate connection duration for logging
            connection_duration = time.time() - connection_start_time
            
            # Wait before reconnecting (unless stopping)
            if not self.should_stop:
                logger.info(f"🔄 Connection lasted {connection_duration:.1f}s. Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                
                # Exponential backoff with maximum delay
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            
    async def fetch_items_list(self, force_refresh=False):
        """Fetch and store the items list"""
        global available_items
        try:
            if force_refresh:
                logger.info("Force refreshing items list...")
            
            response = await self.client.get_items_list(current_page=1, page_size=100)
            if response and response.get("code") == 0:
                # Extract items from response - they're in 'rows', not 'items'
                items_data = response.get("values", {})
                available_items = items_data.get("rows", [])
                
                logger.info(f"Fetched {len(available_items)} items")
                
                # Add custom names to items before sending
                items_with_custom_names = []
                for item in available_items:
                    item_copy = item.copy()
                    item_copy['display_name'] = get_display_name(item)
                    items_with_custom_names.append(item_copy)
                
                # Emit to frontend
                socketio.emit('items_updated', {
                    'items': items_with_custom_names,
                    'total': len(available_items),
                    'is_name_update': False
                })
            else:
                logger.error(f"Failed to fetch items: {response}")
        except Exception as e:
            logger.error(f"Error fetching items: {e}")
    
    async def handle_galbot_message(self, message):
        """Handle messages from Galbot WebSocket"""
        if message.get("op") == "galbot_push":
            service = message.get("service")
            if service == "/expo/goods/order/states/push":
                await self.handle_state_update(message)
        
        # Also handle regular responses
        request_id = message.get("request_id")
        if request_id in self.client.pending_requests:
            request_type = self.client.pending_requests[request_id]
            logger.info(f"Received response for {request_type}: {message.get('status', 'unknown status')}")
    
    async def handle_state_update(self, message):
        """Handle state update messages from Galbot - simplified to just update internal state"""
        global current_tasks
        
        values = message.get("values", {})
        state = values.get("states")
        task_id = str(values.get("task_id", ""))
        
        if task_id and state:
            # Store task info for reference
            current_tasks[task_id] = {
                'state': state,
                'timestamp': message.get("timestamp", ""),
                'message': message
            }
            
            logger.info(f"Websocket: Task {task_id} state: {state}")
            
            # Update internal robot state based on websocket updates
            # Only update if robot is currently operating (ignore old updates)
            current_robot_state = get_robot_state()
            if current_robot_state['state'] == RobotState.OPERATING.value:
                if state == "START":
                    # Robot started working - keep state as OPERATING
                    logger.info(f"🤖 Robot confirmed started working")
                elif state in ["SUCCEED", "SUCCEEDED"]:
                    # Operation succeeded
                    logger.info(f"🤖 Websocket confirmed: Operation succeeded")
                    set_robot_state(RobotState.SUCCEEDED)
                    # Reset to IDLE after a brief moment
                    threading.Timer(2.0, lambda: set_robot_state(RobotState.IDLE)).start()
                elif state in ["FAIL", "FAILED"]:
                    # Operation failed
                    logger.info(f"🤖 Websocket confirmed: Operation failed")
                    set_robot_state(RobotState.FAILED)
                    # Reset to IDLE after a brief moment
                    threading.Timer(2.0, lambda: set_robot_state(RobotState.IDLE)).start()
            
            # Emit to frontend for logging/display purposes
            socketio.emit('task_state_update', {
                'task_id': task_id,
                'state': state,
                'message': self.get_state_message(state)
            })
    
    def get_state_message(self, state):
        """Get user-friendly message for state"""
        messages = {
            "START": "Item picking started...",
            "SUCCEED": "Item successfully picked!",
            "FAILED": "Item picking failed!",
            "FAIL": "Item picking failed!"  # Handle both FAIL and FAILED
        }
        return messages.get(state, f"Unknown state: {state}")
    


    async def pick_item(self, sku_id, quantity=1):
        """Pick an item by SKU ID"""
        try:
            response = await self.client.pick_item(sku_id, quantity)
            logger.info(f"Pick item response: {response}")
            return response
        except Exception as e:
            logger.error(f"Error picking item: {e}")
            return None
    
    def start_background_task(self):
        """Start the background WebSocket task"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.connect_and_listen())
        
        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        logger.info("Background WebSocket task started")
    
    def stop(self):
        """Stop the background task"""
        global state_timeout_timer
        self.should_stop = True
        self.is_connected = False
        
        # Cancel any active timeout timer
        if state_timeout_timer:
            state_timeout_timer.cancel()
            state_timeout_timer = None
            logger.info("🔧 Cancelled robot state timeout timer")
            
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)

# Load custom names on startup
load_custom_names()

# Initialize the AutoDroid web app
autodroid_app = AutoDroidWebApp()

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    """Return a simple favicon response to avoid 404 errors"""
    return '', 204

@app.route('/images/<filename>')
def serve_image(filename):
    """Serve product images from the templates directory"""
    try:
        return send_from_directory('templates', filename)
    except FileNotFoundError:
        # Return a placeholder or 404 if image not found
        return '', 404

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files like logos"""
    try:
        return send_from_directory('static', filename)
    except FileNotFoundError:
        return '', 404

@app.route('/api/items')
def get_items():
    """API endpoint to get available items"""
    global available_items
    # Add custom names to items
    items_with_custom_names = []
    for item in available_items:
        item_copy = item.copy()
        item_copy['display_name'] = get_display_name(item)
        items_with_custom_names.append(item_copy)
    
    return jsonify({
        'items': items_with_custom_names,
        'total': len(available_items)
    })

@app.route('/api/pick_item', methods=['POST'])
def api_pick_item():
    """API endpoint to pick an item - waits for completion and returns final result"""
    data = request.get_json()
    sku_id = data.get('sku_id')
    quantity = data.get('quantity', 1)
    timeout = data.get('timeout', 180)  # Default 3 minutes, can be overridden
    
    if not sku_id:
        return jsonify({'error': 'sku_id is required'}), 400
    
    if not autodroid_app.is_connected:
        return jsonify({'error': 'Not connected to AutoDroid. Please wait for reconnection.'}), 503
    
    # Check robot state before allowing new operation
    current_state = get_robot_state()
    if current_state['state'] != RobotState.IDLE.value:
        return jsonify({
            'error': f'Robot is currently {current_state["state"]}. Please wait for operation to complete.',
            'robot_state': current_state
        }), 409  # Conflict status code
    
    def pick_and_wait():
        """Simplified pick and wait - just wait for any non-operating state"""
        try:
            if autodroid_app.loop and not autodroid_app.loop.is_closed():
                # Send the pick request and immediately set to OPERATING
                async def send_pick_request():
                    try:
                        # Send the pick message without waiting for response
                        message = {
                            "op": "call_galbot",
                            "service": "/expo/goods/order",
                            "args": {
                                "sku_ids": [{"sku_id": sku_id, "quantity": quantity}]
                            }
                        }
                        await autodroid_app.client.send_message(message)
                        logger.info(f"Sent pick request for SKU {sku_id}")
                        return True
                    except Exception as e:
                        logger.error(f"Error sending pick request: {e}")
                        return False
                
                # Send the request
                future = asyncio.run_coroutine_threadsafe(send_pick_request(), autodroid_app.loop)
                send_success = future.result(timeout=5.0)
                
                if not send_success:
                    return {'success': False, 'error': 'Failed to send pick request'}
                
                # Set robot state to OPERATING (websocket will update this when it gets state changes)
                set_robot_state(RobotState.OPERATING)
                
                # Wait for any non-operating state (SUCCESS, FAILED, or IDLE)
                start_time = time.time()
                poll_interval = 0.5  # Check every 500ms
                websocket_check_interval = 10  # Check for websocket state updates every 10 seconds
                last_websocket_check = start_time
                
                logger.info(f"Waiting for robot to finish operating (any non-operating state)...")
                
                while time.time() - start_time < timeout:
                    current_state = get_robot_state()
                    state_value = current_state['state']
                    current_time = time.time()
                    
                    # Return on any non-operating state
                    if state_value != RobotState.OPERATING.value:
                        duration = current_time - start_time
                        
                        if state_value == RobotState.SUCCEEDED.value:
                            return {
                                'success': True,
                                'result': 'succeeded',
                                'message': 'Item picked successfully',
                                'robot_state': current_state,
                                'duration_seconds': duration
                            }
                        elif state_value == RobotState.FAILED.value:
                            return {
                                'success': False,
                                'result': 'failed',
                                'message': 'Item picking failed',
                                'robot_state': current_state,
                                'duration_seconds': duration
                            }
                        elif state_value == RobotState.IDLE.value:
                            # Robot went to IDLE - could be success, failure, or reset due to timeout
                            duration = current_time - start_time
                            if duration > 90:  # If it took longer than 90 seconds, likely timed out
                                logger.warning(f"Robot operation took {duration:.1f}s - likely timed out")
                                return {
                                    'success': False,
                                    'result': 'timeout',
                                    'message': 'Operation likely timed out (robot took too long)',
                                    'robot_state': current_state,
                                    'duration_seconds': duration
                                }
                            else:
                                # Quick completion, assume success
                                return {
                                    'success': True,
                                    'result': 'completed',
                                    'message': 'Operation completed (robot returned to idle)',
                                    'robot_state': current_state,
                                    'duration_seconds': duration
                                }
                    
                    # Periodically check if websocket is working and log connection status
                    if current_time - last_websocket_check >= websocket_check_interval:
                        connection_status = "connected" if autodroid_app.is_connected else "disconnected"
                        elapsed = current_time - start_time
                        logger.info(f"⏳ Still waiting... {elapsed:.1f}s elapsed, websocket: {connection_status}")
                        last_websocket_check = current_time
                    
                    # Sleep before next poll
                    time.sleep(poll_interval)
                
                # Timeout reached - robot still operating
                final_state = get_robot_state()
                return {
                    'success': False,
                    'result': 'timeout',
                    'message': f'Operation timed out after {timeout} seconds',
                    'robot_state': final_state,
                    'duration_seconds': timeout
                }
                
            else:
                return {'success': False, 'error': 'Not connected to AutoDroid'}
                
        except Exception as e:
            logger.error(f"Error in pick_and_wait: {e}")
            return {'success': False, 'error': str(e)}
    
    try:
        result = pick_and_wait()
        
        if result['success']:
            return jsonify(result), 200
        else:
            # Return appropriate error code based on the error type
            if 'timeout' in result.get('result', ''):
                return jsonify(result), 408  # Request Timeout
            elif 'failed' in result.get('result', ''):
                return jsonify(result), 422  # Unprocessable Entity (robot failed)
            else:
                return jsonify(result), 500  # Internal Server Error
            
    except Exception as e:
        logger.error(f"Error in pick_item API: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get connection status"""
    return jsonify({
        'connected': autodroid_app.is_connected,
        'items_count': len(available_items),
        'active_tasks': len(current_tasks),
        'robot_state': get_robot_state()
    })

@app.route('/api/robot/state')
def get_robot_state_api():
    """Get current robot state"""
    return jsonify(get_robot_state())

@app.route('/api/robot/reset', methods=['POST'])
def reset_robot_state():
    """Manually reset robot state to IDLE (emergency reset)"""
    try:
        old_state = get_robot_state()
        set_robot_state(RobotState.IDLE)
        logger.info(f"🔧 Manual reset: Robot state reset from {old_state['state']} to IDLE")
        
        return jsonify({
            'success': True,
            'message': 'Robot state reset to IDLE',
            'previous_state': old_state,
            'current_state': get_robot_state()
        })
    except Exception as e:
        logger.error(f"Error resetting robot state: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_name', methods=['POST'])
def update_item_name():
    """Update custom display name for an item"""
    global custom_names
    data = request.get_json()
    sku_id = str(data.get('sku_id'))
    new_name = data.get('name', '').strip()
    
    if not sku_id:
        return jsonify({'error': 'sku_id is required'}), 400
    
    if not new_name:
        return jsonify({'error': 'name is required'}), 400
    
    try:
        custom_names[sku_id] = new_name
        save_custom_names()
        
        # Broadcast updated items to all connected clients
        items_with_custom_names = []
        for item in available_items:
            item_copy = item.copy()
            item_copy['display_name'] = get_display_name(item)
            items_with_custom_names.append(item_copy)
        
        socketio.emit('items_updated', {
            'items': items_with_custom_names,
            'total': len(available_items),
            'is_name_update': True
        })
        
        return jsonify({'success': True, 'message': f'Updated name for SKU {sku_id}'})
    except Exception as e:
        logger.error(f"Error updating name: {e}")
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    emit('connection_status', {'connected': autodroid_app.is_connected})
    
    # Send current robot state
    current_state = get_robot_state()
    emit('robot_state_changed', current_state)
    
    # Send current items if available
    if available_items:
        items_with_custom_names = []
        for item in available_items:
            item_copy = item.copy()
            item_copy['display_name'] = get_display_name(item)
            items_with_custom_names.append(item_copy)
        
        emit('items_updated', {
            'items': items_with_custom_names,
            'total': len(available_items),
            'is_name_update': False
        })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')

@socketio.on('refresh_items')
def handle_refresh_items():
    """Handle request to refresh items with better feedback"""
    logger.info("🔄 Manual refresh requested by user")
    
    if autodroid_app.is_connected:
        # Schedule refresh in the background with force_refresh=True
        def refresh_async():
            try:
                if autodroid_app.loop:
                    logger.info("🔄 Starting hard refresh of items list...")
                    future = asyncio.run_coroutine_threadsafe(
                        autodroid_app.fetch_items_list(force_refresh=True), 
                        autodroid_app.loop
                    )
                    # Wait for completion with timeout
                    future.result(timeout=10.0)
                    logger.info("✅ Hard refresh completed successfully")
                    socketio.emit('status_message', {'message': 'Items refreshed successfully!', 'type': 'success'})
                else:
                    logger.error("❌ No event loop available for refresh")
                    socketio.emit('status_message', {'message': 'Refresh failed - no event loop', 'type': 'error'})
            except Exception as e:
                logger.error(f"❌ Error during refresh: {e}")
                socketio.emit('status_message', {'message': f'Refresh failed: {str(e)}', 'type': 'error'})
        
        threading.Thread(target=refresh_async, daemon=True).start()
        emit('status_message', {'message': 'Hard refreshing items from server...', 'type': 'info'})
    else:
        logger.warning("❌ Refresh requested but not connected to AutoDroid")
        emit('status_message', {'message': 'Cannot refresh - not connected to AutoDroid', 'type': 'error'})

if __name__ == '__main__':
    # Start the background WebSocket task
    autodroid_app.start_background_task()
    
    try:
        # Run the Flask app
        socketio.run(app, host='0.0.0.0', port=1987, debug=True, use_reloader=False)
    finally:
        # Clean up
        autodroid_app.stop()
