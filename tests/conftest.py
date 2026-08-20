"""pytest 全局 conftest — 确保 import data_source 路径"""
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
# 让 `import data_source` 能找到 /home/ubuntu/sida-src/data_source
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))