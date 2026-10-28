import os
import subprocess
from pathlib import Path
import pytest

def test_no_mojibake_in_index_html():
    """Verify that frontend/index.html contains no corrupted mojibake text characters."""
    project_root = Path(__file__).resolve().parent.parent
    index_path = project_root / "frontend" / "index.html"
    
    assert index_path.exists(), "index.html is missing"
    
    with open(index_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    
    # Strip Byte Order Mark if present at the very beginning
    if content.startswith('\ufeff'):
        content = content[1:]
        
    corrupted_patterns = ["â", "ð", "Â", "\ufffd"]
    
    for pat in corrupted_patterns:
        assert pat not in content, f"Corrupted character/mojibake sequence '{pat}' found in index.html"


def test_index_html_favicon_link_valid():
    """Verify that index.html contains a valid favicon link element."""
    project_root = Path(__file__).resolve().parent.parent
    index_path = project_root / "frontend" / "index.html"
    
    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    assert 'rel="icon"' in content or 'rel="shortcut icon"' in content, "index.html does not contain a favicon link element"


def test_critical_js_functions_exist():
    """Verify that all critical JS helper and workflow functions are defined."""
    project_root = Path(__file__).resolve().parent.parent
    index_path = project_root / "frontend" / "index.html"
    
    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    critical_funcs = [
        "processTrainingUploadDocument",
        "renderOcrTraining",
        "renderMainAiFillDebug",
        "renderBackendReview",
        "doAutoFill",
        "clearMainAiFilledValues"
    ]
    
    for func in critical_funcs:
        assert func in content, f"Critical JS function/helper '{func}' is missing from index.html"


def test_js_and_frontend_validators():
    """Execute the external JS syntax/structure checkers and ensure they pass."""
    project_root = Path(__file__).resolve().parent.parent
    
    # 1. Run check_js.js
    res_js = subprocess.run(
        ["node", "tests/check_js.js"],
        cwd=str(project_root),
        capture_output=True,
        text=True
    )
    assert res_js.returncode == 0, f"check_js.js failed with stdout: {res_js.stdout}, stderr: {res_js.stderr}"
    assert "ALL CHECKS PASSED" in res_js.stdout
    
    # 2. Run check_frontend.js
    res_fe = subprocess.run(
        ["node", "check_frontend.js"],
        cwd=str(project_root),
        capture_output=True,
        text=True
    )
    assert res_fe.returncode == 0, f"check_frontend.js failed with stdout: {res_fe.stdout}, stderr: {res_fe.stderr}"
    assert "duplicates 0" in res_fe.stdout
