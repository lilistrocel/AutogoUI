import asyncio
import websockets
import json
import uuid

async def simple_galbot_example():
    """Simple example of using the Galbot WebSocket API"""
    
    # WebSocket URL
    websocket_url = "ws://192.168.50.6/ws?type=expo"
    
    try:
        # Connect to WebSocket
        async with websockets.connect(websocket_url) as websocket:
            print(f"Connected to {websocket_url}")
            
            # Generate a unique request ID
            request_id = str(uuid.uuid4()).replace('-', '')[:12]
            
            # 1. Get items list
            get_items_message = {
                "op": "call_galbot",
                "request_id": request_id,
                "service": "/expo/goods/list",
                "args": {
                    "current_page": 1,
                    "page_size": 48
                }
            }
            
            print("Sending get items request...")
            await websocket.send(json.dumps(get_items_message))
            
            # Receive response
            response = await websocket.recv()
            items_data = json.loads(response)
            print("Items list response:")
            print(json.dumps(items_data, indent=2))
            
            # 2. Pick an item (example with SKU ID 27)
            pick_request_id = str(uuid.uuid4()).replace('-', '')[:12]
            
            pick_item_message = {
                "op": "call_galbot",
                "request_id": pick_request_id,
                "service": "/expo/goods/order",
                "args": {
                    "sku_ids": [{"sku_id": 27, "quantity": 1}]
                }
            }
            
            print("\nSending pick item request...")
            await websocket.send(json.dumps(pick_item_message))
            
            # Receive response
            response = await websocket.recv()
            pick_data = json.loads(response)
            print("Pick item response:")
            print(json.dumps(pick_data, indent=2))
            
            # 3. Listen for state updates
            print("\nListening for state updates...")
            for _ in range(10):  # Listen for up to 10 messages
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    state_data = json.loads(response)
                    print("State update:")
                    print(json.dumps(state_data, indent=2))
                    
                    # Check if it's a state update
                    if (state_data.get("op") == "galbot_push" and 
                        state_data.get("service") == "/expo/goods/order/states/push"):
                        
                        values = state_data.get("values", {})
                        state = values.get("states")
                        task_id = values.get("task_id")
                        
                        print(f"Task {task_id} is in state: {state}")
                        
                        if state in ["SUCCEED", "FAILED"]:
                            print(f"Task completed with state: {state}")
                            break
                            
                except asyncio.TimeoutError:
                    print("No more messages received (timeout)")
                    break
    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(simple_galbot_example())
