"""Export contracts without connecting to or modifying a configured database."""
import json, os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['APP_ENV']='test'
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['MOCK_NOTIFICATIONS']='true'
os.environ['REDIS_URL']=''
from app.main import app
application = app.app if hasattr(app, 'app') and not hasattr(app, 'openapi') else app
output=Path(__file__).resolve().parents[2]/'docs/openapi.json'
output.write_text(json.dumps(application.openapi(),indent=2),encoding='utf-8')
print(f'Exported {len(application.openapi()["paths"])} paths to {output.name}')
