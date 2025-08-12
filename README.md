# Galbot WebSocket Client & Web Interface

This repository contains Python scripts and a web interface to interact with the Galbot WebSocket API for expo goods management.

## Files

- `galbot_websocket_client.py` - Full-featured WebSocket client class with comprehensive functionality
- `simple_galbot_example.py` - Simple example demonstrating basic usage
- `app.py` - Flask web application with real-time WebSocket integration
- `start_galbot_web.py` - Easy startup script for the web interface
- `templates/index.html` - Beautiful, responsive web interface
- `requirements.txt` - Python dependencies

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### 🌐 Web Interface (Recommended)

The easiest way to use Galbot is through the web interface:

```bash
python start_galbot_web.py
```

Then open your browser and go to: **http://localhost:5000**

The web interface provides:
- 🛒 **Interactive Item Selection**: Browse available items as clickable cards
- 📱 **Responsive Design**: Works on desktop, tablet, and mobile
- 🔄 **Real-time Updates**: Live status updates for picking operations
- 📊 **Activity Logging**: Monitor all operations and their status
- 🎨 **Beautiful UI**: Modern, gradient design with smooth animations

### 📋 Command Line Example

Run the simple example to see basic functionality:

```bash
python simple_galbot_example.py
```

This script will:
1. Connect to the WebSocket
2. Get the items list
3. Pick an item (SKU ID 27)
4. Listen for state updates

### Advanced Usage

Use the `GalbotWebSocketClient` class for more advanced functionality:

```python
from galbot_websocket_client import GalbotWebSocketClient

async def example():
    client = GalbotWebSocketClient()
    
    # Connect
    await client.connect()
    
    # Get items
    items = await client.get_items_list(current_page=1, page_size=48)
    
    # Pick an item
    result = await client.pick_item(sku_id=27, quantity=1)
    
    # Disconnect
    await client.disconnect()
```

## API Reference

### WebSocket URL
```
ws://192.168.50.6/ws?type=expo
```

### Available Operations

#### Get Items List
```json
{
    "op": "call_galbot",
    "request_id": "unique_id",
    "service": "/expo/goods/list",
    "args": {
        "current_page": 1,
        "page_size": 48
    }
}
```

#### Pick Item
```json
{
    "op": "call_galbot",
    "request_id": "unique_id",
    "service": "/expo/goods/order",
    "args": {
        "sku_ids": [{"sku_id": 27, "quantity": 1}]
    }
}
```

### State Updates

The WebSocket will send state updates for pick operations:

- **START**: Item picking has started
- **SUCCEED**: Item was successfully picked
- **FAILED**: Item picking failed

Example state update:
```json
{
    "code": 0,
    "msg": "",
    "op": "galbot_push",
    "request_id": "request_id",
    "result": true,
    "service": "/expo/goods/order/states/push",
    "values": {
        "states": "SUCCEED",
        "task_id": "129"
    }
}
```

## Features

### WebSocket Client Features
- Asynchronous WebSocket communication
- Automatic request ID generation
- State tracking for pick operations
- Comprehensive error handling
- Logging support
- Task completion waiting with timeout

### Web Interface Features
- 🖥️ **Real-time Dashboard**: Live connection status and item counts
- 🛍️ **Interactive Item Grid**: Beautiful cards showing item details, prices, and descriptions
- 🎯 **One-Click Picking**: Simple button clicks to pick items
- 🔄 **Live Status Updates**: Real-time feedback on picking operations (START → SUCCEED/FAILED)
- 📱 **Mobile Responsive**: Works perfectly on phones and tablets
- 🎨 **Modern Design**: Gradient backgrounds, smooth animations, and hover effects
- 📋 **Activity Log**: Scrollable log of all operations with timestamps
- 🔌 **Auto-Reconnection**: Handles connection drops gracefully
- 🚨 **Visual Notifications**: Toast notifications for important events
- 📊 **Status Indicators**: Color-coded states for each item (picking, success, failed)

## Error Handling

The scripts include comprehensive error handling for:
- Connection failures
- Message parsing errors
- Timeout scenarios
- WebSocket disconnections

## Configuration

You can modify the WebSocket URL in the scripts if needed:
```python
websocket_url = "ws://YOUR_IP_ADDRESS/ws?type=expo"
```
