from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import asyncio
import json
import logging
import threading
import os
from galbot_websocket_client import GalbotWebSocketClient

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

class AutoDroidWebApp:
    def __init__(self):
        self.client = GalbotWebSocketClient()
        self.is_connected = False
        self.loop = None
        self.thread = None
        self.should_stop = False
        self.items_loaded = False  # Flag to track if items have been loaded once
        
    async def connect_and_listen(self):
        """Connect to Galbot WebSocket and start listening"""
        while not self.should_stop:  # Keep trying to reconnect
            try:
                logger.info(f"Attempting to connect to Galbot WebSocket at {self.client.websocket_url}")
                
                if await self.client.connect():
                    self.is_connected = True
                    logger.info("Connected to Galbot WebSocket")
                    
                    # Emit connection status to frontend
                    socketio.emit('connection_status', {'connected': True})
                    
                    # Get initial items list only on first connection
                    if not self.items_loaded:
                        await self.fetch_items_list()
                        self.items_loaded = True
                        logger.info("Items loaded on initial connection")
                    else:
                        logger.info("Reconnected - skipping item reload (items already loaded)")
                    
                    # Start listening for messages
                    while self.is_connected and not self.should_stop:
                        try:
                            message = await self.client.receive_message()
                            await self.handle_galbot_message(message)
                        except Exception as e:
                            logger.error(f"Error receiving message: {e}")
                            break
                else:
                    logger.error("Failed to connect to Galbot WebSocket")
                    
            except Exception as e:
                logger.error(f"Error in connect_and_listen: {e}")
            finally:
                self.is_connected = False
                socketio.emit('connection_status', {'connected': False})
                
            # Wait before reconnecting (unless stopping)
            if not self.should_stop:
                logger.info("Attempting to reconnect in 5 seconds...")
                await asyncio.sleep(5)
            
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
        """Handle state update messages from Galbot"""
        global current_tasks
        
        values = message.get("values", {})
        state = values.get("states")
        task_id = str(values.get("task_id", ""))
        
        if task_id:
            current_tasks[task_id] = {
                'state': state,
                'timestamp': message.get("timestamp", ""),
                'message': message
            }
            
            logger.info(f"Task {task_id} state: {state}")
            
            # Emit to frontend
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
    
    async def send_pick_request(self, sku_id: int, quantity: int = 1):
        """Send pick request without waiting for response"""
        try:
            # Just send the message, don't wait for response
            message = {
                "op": "call_galbot",
                "service": "/expo/goods/order",
                "args": {
                    "sku_ids": [{"sku_id": sku_id, "quantity": quantity}]
                }
            }
            await self.client.send_message(message)
            logger.info(f"Sent pick request for SKU {sku_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending pick request: {e}")
            return False

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
        self.should_stop = True
        self.is_connected = False
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
    """API endpoint to pick an item"""
    data = request.get_json()
    sku_id = data.get('sku_id')
    quantity = data.get('quantity', 1)
    
    if not sku_id:
        return jsonify({'error': 'sku_id is required'}), 400
    
    if not autodroid_app.is_connected:
        return jsonify({'error': 'Not connected to AutoDroid. Please wait for reconnection.'}), 503
    
    # Schedule the pick operation as fire-and-forget
    def pick_async():
        try:
            if autodroid_app.loop and not autodroid_app.loop.is_closed():
                # Just send the pick request without waiting for response
                # The UI will get updates via WebSocket state changes
                asyncio.run_coroutine_threadsafe(
                    autodroid_app.send_pick_request(sku_id, quantity), 
                    autodroid_app.loop
                )
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"Error in pick_async: {e}")
            return False
    
    try:
        success = pick_async()
        
        if success:
            return jsonify({'success': True, 'message': 'Pick request sent successfully'})
        else:
            return jsonify({'error': 'Failed to send pick request - not connected'}), 503
            
    except Exception as e:
        logger.error(f"Error in pick_item API: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get connection status"""
    return jsonify({
        'connected': autodroid_app.is_connected,
        'items_count': len(available_items),
        'active_tasks': len(current_tasks)
    })

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
    """Handle request to refresh items"""
    if autodroid_app.is_connected:
        # Schedule refresh in the background with force_refresh=True
        def refresh_async():
            if autodroid_app.loop:
                asyncio.run_coroutine_threadsafe(
                    autodroid_app.fetch_items_list(force_refresh=True), 
                    autodroid_app.loop
                )
        
        threading.Thread(target=refresh_async, daemon=True).start()
        emit('status_message', {'message': 'Manually refreshing items...', 'type': 'info'})
    else:
        emit('status_message', {'message': 'Not connected to AutoDroid', 'type': 'error'})

if __name__ == '__main__':
    # Start the background WebSocket task
    autodroid_app.start_background_task()
    
    try:
        # Run the Flask app
        socketio.run(app, host='0.0.0.0', port=1987, debug=True, use_reloader=False)
    finally:
        # Clean up
        autodroid_app.stop()
