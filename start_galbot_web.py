#!/usr/bin/env python3
"""
AutoDroid Web Interface Startup Script

This script starts the AutoDroid web interface which provides:
- Automatic connection to the AutoDroid WebSocket API
- Web interface for viewing available items
- One-click item picking with real-time status updates
- Activity logging and status monitoring

Usage:
    python start_galbot_web.py

The web interface will be available at: http://localhost:1987
"""

import sys
import logging
import time
from app import app, socketio, autodroid_app

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Main startup function"""
    print("\n" + "="*60)
    print("🤖 AUTODROID WEB INTERFACE")
    print("="*60)
    print()
    print("Starting AutoDroid Web Interface...")
    print("This will provide a web UI for picking items from the expo display.")
    print()
    print("Features:")
    print("  ✅ Real-time connection to AutoDroid WebSocket API")
    print("  ✅ Interactive web interface with splash screen")
    print("  ✅ Live status updates for picking operations")
    print("  ✅ Activity logging and monitoring")
    print("  ✅ Fullscreen kiosk mode")
    print("  ✅ Cool animations and transitions")
    print()
    print("WebSocket Target: ws://192.168.50.6/ws?type=expo")
    print("Web Interface:    http://localhost:1987")
    print()
    print("="*60)
    print()
    
    try:
        # Start the background WebSocket task
        logger.info("Starting background WebSocket connection...")
        autodroid_app.start_background_task()
        
        # Give it a moment to establish connection
        time.sleep(2)
        
        print("✅ Background WebSocket task started")
        print("🌐 Starting web server...")
        print()
        print("Access the web interface at: http://localhost:1987")
        print("Press Ctrl+C to stop the server")
        print()
        
        # Run the Flask app with SocketIO
        socketio.run(
            app, 
            host='0.0.0.0', 
            port=1987, 
            debug=False,  # Set to False for production
            use_reloader=False,  # Prevent double startup
            log_output=True
        )
        
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        logger.info("Received interrupt signal")
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        logger.error(f"Error starting server: {e}")
        sys.exit(1)
    finally:
        # Clean up
        logger.info("Cleaning up...")
        autodroid_app.stop()
        print("✅ Cleanup complete")
        print("👋 Goodbye!")

if __name__ == "__main__":
    main()
