from qc_agent.scaffold.fastapi_paths import inspect


def test_nested_router_settings_prefix_and_schema(tmp_path):
    files = {
        'app/core/config.py': 'class Settings:\n API_V1_STR: str = "/api/v1"\nsettings = Settings()\n',
        'app/api/routes/utils.py': 'from fastapi import APIRouter\nrouter=APIRouter(prefix="/utils")\n@router.get("/health-check/")\ndef health(): return True\n',
        'app/api/main.py': 'from fastapi import APIRouter\nfrom app.api.routes import utils\napi_router=APIRouter()\napi_router.include_router(utils.router)\n',
        'app/main.py': 'from fastapi import FastAPI\nfrom app.api.main import api_router\nfrom app.core.config import settings\napp=FastAPI(openapi_url=f"{settings.API_V1_STR}/openapi.json")\napp.include_router(api_router,prefix=settings.API_V1_STR)\n',
    }
    for name, text in files.items():
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    paths,schemas,unknown=inspect(tmp_path,files)
    assert paths==['/api/v1/utils/health-check/']
    assert schemas==['/api/v1/openapi.json'] and not unknown


def test_dynamic_prefix_is_not_guessed(tmp_path):
    p=tmp_path/'main.py'
    p.write_text('from fastapi import FastAPI,APIRouter\napp=FastAPI(openapi_url=lookup())\nrouter=APIRouter()\n@router.get("/health")\ndef health(): return True\napp.include_router(router,prefix=lookup())\n')
    paths,schemas,unknown=inspect(tmp_path,['main.py'])
    assert paths==[] and schemas==[] and unknown
