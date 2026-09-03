.PHONY: help env venv deps services-start services-stop \
        seed seed-dev verify db-init db-reset db-grant check-fewshots \
        seed-i4 check-i4 check-uc3 check-uc4 check-security check-pipeline \
        check-pipeline-user check-multiturn check-slots check-slots-update \
        eval eval-quick probe-models compare-models test \
        setup-local api-dev web-dev status

# 优先使用项目虚拟环境；未创建时回退到系统 python3
# 必须用绝对路径：各 target 内会先 cd server，相对路径会被二次拼接
VENV_PY := $(CURDIR)/server/.venv/bin/python
PYTHON  ?= $(shell test -x $(VENV_PY) && echo $(VENV_PY) || echo python3)
SCALE   ?= 1.0
DSN     ?= postgresql+asyncpg://jingguan:jingguan@localhost:5432/jingguan

help: ## 查看帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ─────────────────────────────────────────────────────────────
# 本机环境（macOS / Homebrew）
# ─────────────────────────────────────────────────────────────
env: ## 生成 .env（从 .env.example 复制，已预置本机直连配置）
	@test -f .env || cp .env.example .env
	@echo "  .env 已就绪；生产环境请务必替换 SECRET_KEY / AES_KEY"

venv: ## 创建 Python 3.11 虚拟环境
	test -d server/.venv || /opt/homebrew/opt/python@3.11/bin/python3.11 -m venv server/.venv
	@echo "  虚拟环境：server/.venv"

deps: venv ## 安装后端依赖
	server/.venv/bin/pip install -q "fastapi" "uvicorn[standard]" "pydantic" "pydantic-settings" \
		"sqlalchemy[asyncio]>=2.0" "alembic" "asyncpg" "psycopg[binary]" "redis" \
		"python-jose[cryptography]" "passlib[bcrypt]" "sqlglot>=23" "jinja2" \
		"python-multipart" "httpx" "pytest" "pytest-asyncio" "ruff" \
		"openpyxl"          # 台账导出 Excel（xlsx）
	@echo "  后端依赖安装完成"

services-start: ## 启动本机依赖服务（PostgreSQL / Redis）
	brew services start postgresql@15
	brew services start redis
	@sleep 4
	@redis-cli ping
	@/opt/homebrew/opt/postgresql@15/bin/pg_isready -h localhost

services-stop: ## 停止本机依赖服务
	brew services stop postgresql@15
	brew services stop redis

# ─────────────────────────────────────────────────────────────
# 数据
# ─────────────────────────────────────────────────────────────
seed: ## 造数（满量 6.2 万行）并跑一致性校验
	cd server && $(PYTHON) -m scripts.data_factory.main --scale $(SCALE)

seed-dev: ## 造数（小数据量，本地开发用）
	cd server && $(PYTHON) -m scripts.data_factory.main --scale 0.1

verify: ## 仅执行造数一致性校验
	cd server && $(PYTHON) -m scripts.data_factory.main --verify-only

db-init: ## 建表 + 装载 CSV + 装载后校验
	cd server && $(PYTHON) -m scripts.db_init --dsn "$(DSN)"

db-reset: ## 清空业务数据后重新装载
	cd server && $(PYTHON) -m scripts.db_init --dsn "$(DSN)" --drop

db-grant: ## 仅重新执行只读账号授权
	cd server && $(PYTHON) -m scripts.db_init --dsn "$(DSN)" --grant-only

check-fewshots: ## 校验语义层 Few-shot SQL 全部可执行
	cd server && $(PYTHON) -m scripts.eval.check_fewshots --verbose

check-slots: ## 槽位抽取回归（黄金快照比对，零 LLM 调用，需数据库）
	cd server && $(PYTHON) -m scripts.check_slots_golden

check-slots-update: ## 重新生成槽位抽取基线（确认行为变更有意为之后执行）
	cd server && $(PYTHON) -m scripts.check_slots_golden --update

test: ## 后端全量测试
	cd server && $(PYTHON) -m pytest tests/ -q

check-pipeline: ## 问数链路端到端基线测试（需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_pipeline

check-pipeline-user: ## 以受限账号验证数据权限隔离
	cd server && $(PYTHON) -u -m scripts.eval.check_pipeline --username zhangsan --limit 3

check-multiturn: ## 多轮对话端到端验证（UC-2，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_multiturn

eval: ## 100 条准确率评测（需后端已启动，约 1.5 小时）
	cd server && $(PYTHON) -u -m scripts.eval.run_eval --output server/data/eval_result.json

eval-quick: ## 抽样 20 条快速评测
	cd server && $(PYTHON) -u -m scripts.eval.run_eval --limit 20

probe-models: ## 探测候选模型可用性与延迟（换模型前先跑）
	cd server && $(PYTHON) -u -m scripts.eval.probe_models

compare-models: ## 用真实链路对比模型的 SQL 质量与耗时
	cd server && $(PYTHON) -u -m scripts.eval.compare_models

check-i4: ## I4 运营闭环接口冒烟（配置/模型/反馈/快捷提问/日志，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_i4

check-ledger: ## 台账接口验证（含权限隔离与注入用例，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_ledger

check-semantic: ## 语义层管理接口验证（CRUD/权限/样本 SQL 校验，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_semantic

bench-qa: ## 问数并发压测（默认 10 并发 × 2 轮，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.bench_qa

warmup-cache: ## 预热问数缓存（演示/压测前跑，可让 P95 从 21s 降到 0.1s）
	cd server && $(PYTHON) -u -m scripts.warmup_cache

check-security: ## 安全用例：越权/注入/鉴权边界（27 项，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_security

check-uc3: ## 数据权限隔离验证（UC-3，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_uc3

check-uc4: ## 反馈闭环端到端验证（UC-4，需后端已启动）
	cd server && $(PYTHON) -u -m scripts.eval.check_uc4

seed-i4: ## 同步 I4 种子（禁用无 Key 模型 + 导入 .env 模型 + 推荐快捷提问）
	cd server && $(PYTHON) -u -m scripts.seed_i4

# ─────────────────────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────────────────────
api-dev: ## 启动后端（热重载）http://127.0.0.1:8000/docs
	cd server && $(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

web-dev: ## 启动前端 http://127.0.0.1:5180
	cd web && npm run dev

setup-local: env deps services-start db-grant seed db-init ## 一键搭好本机环境（首次执行）

status: ## 查看本机服务与数据状态
	@echo "── 服务 ──"
	@redis-cli ping 2>/dev/null || echo "  redis      未运行"
	@/opt/homebrew/opt/postgresql@15/bin/pg_isready -h localhost 2>/dev/null || echo "  postgres   未运行"
	@echo "── 接口 ──"
	@curl -s --max-time 5 http://127.0.0.1:8000/api/v1/health | head -c 150 || echo "  后端未启动"
	@echo ""
	@curl -s --max-time 5 http://127.0.0.1:5180/ -o /dev/null -w "  前端 HTTP %{http_code}\n" || echo "  前端未启动"
