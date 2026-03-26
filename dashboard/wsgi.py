"""
WSGI entry point for production deployment with gunicorn
"""
import sys
import os
import yaml

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from web.server import create_app

# Load config to get database path
config_file = 'config.yaml'
with open(config_file, 'r') as f:
    config = yaml.safe_load(f)

db_path = config['database']['path']

# Create Flask app
app = create_app(
    db_path=db_path,
    config_file=config_file
)

if __name__ == '__main__':
    app.run()
