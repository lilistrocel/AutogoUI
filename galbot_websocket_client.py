import asyncio
import websockets
import json
import uuid
from typing import Dict, List, Optional
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GalbotWebSocketClient:
    def __init__(self, websocket_url: str = "ws://192.168.50.6/ws?type=expo"):
        self.websocket_url = websocket_url
        self.websocket = None
        self.pending_requests = {}
        self.task_states = {}
        
    async def connect(self):
        """Connect to the WebSocket server"""
        try:
            self.websocket = await websockets.connect(self.websocket_url)
            logger.info(f"Connected to {self.websocket_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from the WebSocket server"""
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from WebSocket")
    
    def generate_request_id(self) -> str:
        """Generate a unique request ID"""
        return str(uuid.uuid4()).replace('-', '')[:12]
    
    async def send_message(self, message: Dict) -> str:
        """Send a message to the WebSocket server"""
        if not self.websocket:
            raise Exception("Not connected to WebSocket")
        
        request_id = message.get("request_id", self.generate_request_id())
        message["request_id"] = request_id
        
        try:
            await self.websocket.send(json.dumps(message))
            logger.info(f"Sent message: {message.get('op', 'unknown')} -> {message.get('service', 'no-service')}")
            return request_id
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise
    
    async def receive_message(self) -> Dict:
        """Receive a message from the WebSocket server"""
        if not self.websocket:
            raise Exception("Not connected to WebSocket")
        
        try:
            message = await self.websocket.recv()
            data = json.loads(message)
            # Only log essential info about received messages
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
                logger.info(f"Received: {msg_type} from {service}")
            return data
        except Exception as e:
            logger.error(f"Failed to receive message: {e}")
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
