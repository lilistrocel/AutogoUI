import asyncio
import websockets
import json
import uuid
from typing import Dict, List, Optional
import logging
import time
import ssl

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GalbotWebSocketClient:
    def __init__(self, websocket_url: str = "ws://192.168.50.6/ws?type=expo"):
        self.websocket_url = websocket_url
        self.websocket = None
        self.pending_requests = {}
        self.task_states = {}
        
        # Connection health monitoring
        self.last_pong_time = 0
        self.ping_interval = 30  # Send ping every 30 seconds
        self.ping_timeout = 10   # Wait 10 seconds for pong response
        self.is_healthy = False
        self.ping_task = None
        
    async def connect(self):
        """Connect to the WebSocket server with proper connection parameters"""
        try:
            logger.debug("🔍 DEBUG: Starting connection process")
            
            # Close existing connection if any
            if self.websocket:
                logger.debug("🔍 DEBUG: Closing existing websocket")
                try:
                    await self.websocket.close()
                except Exception as e:
                    logger.debug(f"🔍 DEBUG: Error closing existing websocket: {e}")
                    pass
                self.websocket = None
            
            # Connect with proper parameters for stability
            connect_timeout = 10  # 10 second connection timeout
            
            # Create SSL context that's more permissive for local connections
            ssl_context = None
            if self.websocket_url.startswith('wss://'):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            
            # Connect with basic parameters for maximum compatibility
            connect_kwargs = {
                'ping_interval': None,  # We'll handle our own ping/pong
                'ping_timeout': None,
            }
            
            # Add SSL context if needed
            if ssl_context:
                connect_kwargs['ssl'] = ssl_context
                
            # Try to add optional parameters if supported
            try:
                # Test if newer parameters are supported
                import inspect
                sig = inspect.signature(websockets.connect)
                if 'close_timeout' in sig.parameters:
                    connect_kwargs['close_timeout'] = 5
                if 'max_size' in sig.parameters:
                    connect_kwargs['max_size'] = 2**20  # 1MB
                if 'compression' in sig.parameters:
                    connect_kwargs['compression'] = None
            except Exception:
                pass  # Use basic parameters only
                
            self.websocket = await asyncio.wait_for(
                websockets.connect(self.websocket_url, **connect_kwargs),
                timeout=connect_timeout
            )
            
            self.last_pong_time = time.time()
            self.is_healthy = True
            
            # Disable ping/pong for now to avoid compatibility issues
            # TODO: Re-enable once we identify the right ping/pong implementation
            # if self.ping_task:
            #     self.ping_task.cancel()
            # self.ping_task = asyncio.create_task(self._ping_loop())
            
            logger.info(f"Connected to {self.websocket_url} with health monitoring")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Connection timeout after {connect_timeout}s")
            return False
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def _ping_loop(self):
        """Background task to monitor connection health with ping/pong"""
        while self.websocket:
            try:
                await asyncio.sleep(self.ping_interval)
                
                if self.websocket:
                    # Check if connection is closed (compatible with different websockets versions)
                    try:
                        is_closed = getattr(self.websocket, 'closed', False)
                        if is_closed:
                            break
                    except Exception:
                        pass  # Continue with ping attempt
                    # Try to send ping if supported, otherwise just assume healthy
                    try:
                        if hasattr(self.websocket, 'ping'):
                            pong_waiter = await self.websocket.ping()
                            try:
                                await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout)
                                self.last_pong_time = time.time()
                                self.is_healthy = True
                                logger.debug("Ping/pong successful - connection healthy")
                            except asyncio.TimeoutError:
                                logger.warning(f"Ping timeout - no pong received within {self.ping_timeout}s")
                                self.is_healthy = False
                                break
                        else:
                            # No ping support, just mark as healthy and continue
                            self.last_pong_time = time.time()
                            self.is_healthy = True
                            logger.debug("No ping support - assuming connection healthy")
                    except Exception as ping_error:
                        logger.warning(f"Ping failed: {ping_error} - assuming connection healthy")
                        self.last_pong_time = time.time()
                        self.is_healthy = True
                        
            except Exception as e:
                logger.error(f"Error in ping loop: {e}")
                self.is_healthy = False
                break
        
        logger.info("Ping loop ended - connection no longer healthy")
        self.is_healthy = False
    
    async def disconnect(self):
        """Properly disconnect from the WebSocket server"""
        self.is_healthy = False
        
        # Cancel ping task
        if self.ping_task:
            self.ping_task.cancel()
            self.ping_task = None
            
        # Close websocket connection
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info("Disconnected from WebSocket")
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")
            finally:
                self.websocket = None
    
    def generate_request_id(self) -> str:
        """Generate a unique request ID"""
        return str(uuid.uuid4()).replace('-', '')[:12]
    
    async def send_message(self, message: Dict) -> str:
        """Send a message to the WebSocket server with retry logic"""
        if not self.websocket:
            raise Exception("Not connected to WebSocket")
        
        # Check if connection is closed (compatible with different websockets versions)
        try:
            is_closed = getattr(self.websocket, 'closed', False)
            if is_closed:
                raise Exception("WebSocket is closed")
        except Exception:
            # If we can't check closed status, continue and let send() handle it
            pass
        
        request_id = message.get("request_id", self.generate_request_id())
        message["request_id"] = request_id
        
        try:
            message_json = json.dumps(message)
            await asyncio.wait_for(self.websocket.send(message_json), timeout=5.0)
            logger.info(f"Sent message: {message.get('op', 'unknown')} -> {message.get('service', 'no-service')}")
            return request_id
        except asyncio.TimeoutError:
            logger.error("Send message timeout after 5s")
            self.is_healthy = False
            raise Exception("Send timeout")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self.is_healthy = False
            raise
    
    async def receive_message(self) -> Dict:
        """Receive a message from the WebSocket server with comprehensive debugging"""
        logger.debug("🔍 receive_message() called")
        
        if not self.websocket:
            logger.error("❌ DEBUG: No websocket object")
            raise Exception("Not connected to WebSocket")
        
        # Check if connection is closed (compatible with different websockets versions)
        try:
            is_closed = getattr(self.websocket, 'closed', False)
            logger.debug(f"🔍 DEBUG: Connection closed status: {is_closed}")
            if is_closed:
                logger.error("❌ DEBUG: WebSocket is closed")
                raise Exception("WebSocket is closed")
        except Exception as e:
            logger.debug(f"🔍 DEBUG: Could not check closed status: {e}")
            # If we can't check closed status, continue and let recv() handle it
            pass
        
        logger.debug("🔍 DEBUG: About to call websocket.recv()")
        
        try:
            # Receive without timeout - let the websocket handle keepalive
            message = await self.websocket.recv()
            logger.debug(f"🔍 DEBUG: Received raw message: {len(message) if message else 0} bytes")
            
            # Parse JSON
            try:
                data = json.loads(message)
                logger.debug(f"🔍 DEBUG: Parsed JSON successfully: {data.get('op', 'unknown')}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ DEBUG: Invalid JSON received: {e}")
                logger.error(f"❌ DEBUG: Raw message: {message[:200]}...")
                raise Exception(f"Invalid JSON: {e}")
            
            # Log essential info about received messages
            msg_type = data.get('op', 'unknown')
            service = data.get('service', '')
            
            if msg_type == 'galbot_push' and '/states/push' in service:
                # Log state updates concisely
                values = data.get('values', {})
                task_id = values.get('task_id', 'unknown')
                state = values.get('states', 'unknown')
                logger.info(f"State update: Task {task_id} -> {state}")
            elif msg_type == 'galbot_resp':
                # Log responses concisely
                status = data.get('status', 'unknown')
                logger.info(f"Response: {status} for {service}")
            else:
                # Log other messages without full content
                logger.debug(f"Received: {msg_type} from {service}")
                
            logger.debug("🔍 DEBUG: receive_message() completing successfully")
            return data
            
        # No timeout handling needed since we removed the timeout
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"❌ DEBUG: WebSocket connection closed during recv(): {e}")
            self.is_healthy = False
            raise
        except Exception as e:
            logger.error(f"❌ DEBUG: Failed to receive message: {type(e).__name__}: {e}")
            logger.error(f"❌ DEBUG: WebSocket state: {getattr(self.websocket, 'state', 'unknown')}")
            self.is_healthy = False
            raise
    
    async def get_items_list(self, current_page: int = 1, page_size: int = 48) -> Optional[Dict]:
        """Get the list of available items"""
        message = {
            "op": "call_galbot",
            "service": "/expo/goods/list",
            "args": {
                "current_page": current_page,
                "page_size": page_size
            }
        }
        
        request_id = await self.send_message(message)
        self.pending_requests[request_id] = "get_items_list"
        
        # Wait for response
        while True:
            response = await self.receive_message()
            if response.get("request_id") == request_id:
                return response
            else:
                # Handle other messages (like state updates)
                await self.handle_message(response)
    
    async def pick_item(self, sku_id: int, quantity: int = 1) -> Optional[Dict]:
        """Pick an item by SKU ID"""
        message = {
            "op": "call_galbot",
            "service": "/expo/goods/order",
            "args": {
                "sku_ids": [{"sku_id": sku_id, "quantity": quantity}]
            }
        }
        
        request_id = await self.send_message(message)
        self.pending_requests[request_id] = "pick_item"
        
        # Wait for response
        while True:
            response = await self.receive_message()
            if response.get("request_id") == request_id:
                return response
            else:
                # Handle other messages (like state updates)
                await self.handle_message(response)
    
    async def handle_message(self, message: Dict):
        """Handle incoming messages, especially state updates"""
        if message.get("op") == "galbot_push" and message.get("service") == "/expo/goods/order/states/push":
            await self.handle_state_update(message)
        else:
            logger.warning(f"Unhandled message type: {message.get('op', 'unknown')} from {message.get('service', 'no-service')}")
    
    async def handle_state_update(self, message: Dict):
        """Handle state update messages"""
        values = message.get("values", {})
        state = values.get("states")
        task_id = values.get("task_id")
        
        if task_id:
            self.task_states[task_id] = state
            logger.info(f"Task {task_id} state updated to: {state}")
            
            if state == "START":
                logger.info(f"Task {task_id} has started picking item")
            elif state == "SUCCEED":
                logger.info(f"Task {task_id} successfully picked item")
            elif state == "FAILED":
                logger.error(f"Task {task_id} failed to pick item")
    
    async def wait_for_task_completion(self, task_id: str, timeout: int = 30) -> str:
        """Wait for a task to complete (either SUCCEED or FAILED)"""
        start_time = asyncio.get_event_loop().time()
        
        while True:
            if task_id in self.task_states:
                state = self.task_states[task_id]
                if state in ["SUCCEED", "FAILED"]:
                    return state
            
            # Check for timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.error(f"Timeout waiting for task {task_id} to complete")
                return "TIMEOUT"
            
            # Continue listening for messages
            try:
                message = await asyncio.wait_for(self.receive_message(), timeout=1.0)
                await self.handle_message(message)
            except asyncio.TimeoutError:
                continue
    
    async def listen_for_messages(self):
        """Continuously listen for incoming messages"""
        try:
            while True:
                message = await self.receive_message()
                await self.handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error listening for messages: {e}")

async def main():
    """Main function demonstrating the WebSocket client usage"""
    client = GalbotWebSocketClient()
    
    try:
        # Connect to the WebSocket
        if not await client.connect():
            return
        
        # Get items list
        logger.info("Fetching items list...")
        items_response = await client.get_items_list(current_page=1, page_size=48)
        
        if items_response and items_response.get("code") == 0:
            logger.info("Successfully retrieved items list")
            # You can process the items here
            # items = items_response.get("values", {}).get("items", [])
            # for item in items:
            #     print(f"Item: {item}")
        else:
            logger.error(f"Failed to get items list: {items_response}")
            return
        
        # Pick an item (example with SKU ID 27)
        logger.info("Picking item with SKU ID 27...")
        pick_response = await client.pick_item(sku_id=27, quantity=1)
        
        if pick_response and pick_response.get("code") == 0:
            logger.info("Pick item request sent successfully")
            
            # Extract task_id if available
            task_id = pick_response.get("values", {}).get("task_id")
            if task_id:
                logger.info(f"Waiting for task {task_id} to complete...")
                final_state = await client.wait_for_task_completion(str(task_id))
                logger.info(f"Task {task_id} final state: {final_state}")
            else:
                # Continue listening for state updates
                logger.info("Listening for state updates...")
                await asyncio.sleep(10)  # Listen for 10 seconds
        else:
            logger.error(f"Failed to pick item: {pick_response}")
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    # Run the main function
    asyncio.run(main())
