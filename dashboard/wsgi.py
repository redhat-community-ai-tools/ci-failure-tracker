"""
WSGI entry point for production deployment with gunicorn
"""
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from web.server import create_app

# Create Flask app
app = create_app(
    db_path='./data/dashboard.db',
    config_file='config.yaml'
)

if __name__ == '__main__':
    app.run()
