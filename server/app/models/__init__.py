"""SQLAlchemy ORM 模型（与 scripts/sql/*.sql 的表结构一一对应）。

说明：表结构由 SQL 脚本维护（Alembic 基线），这里只做 ORM 映射，
      __table_args__ = {"extend_existing": True} 便于与已存在的表对齐。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class SysUser(Base):
    __tablename__ = "sys_user"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64))
    phone = Column(String(32))
    email = Column(String(128))
    avatar = Column(String(255))
    status = Column(SmallInteger, nullable=False, default=1)
    valid_until = Column(Date)
    last_login_at = Column(DateTime(timezone=True))
    last_login_ip = Column(String(64))
    pwd_must_change = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime(timezone=True))

    roles = relationship("SysRole", secondary="sys_user_role", viewonly=True)

    @property
    def enabled(self) -> bool:
        return self.status == 1


class SysRole(Base):
    __tablename__ = "sys_role"

    id = Column(BigInteger, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    description = Column(String(255))
    is_builtin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SysUserRole(Base):
    __tablename__ = "sys_user_role"

    user_id = Column(BigInteger, ForeignKey("sys_user.id"), primary_key=True)
    role_id = Column(BigInteger, ForeignKey("sys_role.id"), primary_key=True)


class SysMenu(Base):
    __tablename__ = "sys_menu"

    id = Column(BigInteger, primary_key=True)
    parent_id = Column(BigInteger, nullable=False, default=0)
    name = Column(String(64), nullable=False)
    path = Column(String(255))
    component = Column(String(255))
    icon = Column(String(64))
    sort_order = Column(Integer, nullable=False, default=0)
    type = Column(String(16), nullable=False)      # M 目录 / C 菜单 / B 按钮
    perm_code = Column(String(128))
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SysRoleMenu(Base):
    __tablename__ = "sys_role_menu"

    role_id = Column(BigInteger, ForeignKey("sys_role.id"), primary_key=True)
    menu_id = Column(BigInteger, ForeignKey("sys_menu.id"), primary_key=True)
    ops = Column(JSON, nullable=False, default=list)


class SysRoleDataPerm(Base):
    __tablename__ = "sys_role_data_perm"

    id = Column(BigInteger, primary_key=True)
    role_id = Column(BigInteger, ForeignKey("sys_role.id"), nullable=False)
    menu_id = Column(BigInteger, ForeignKey("sys_menu.id"), nullable=False)
    perm_type = Column(String(16), nullable=False)     # view / operate / delete
    unit_codes = Column(JSON, nullable=False, default=list)


class SysAppConfig(Base):
    __tablename__ = "sys_app_config"

    config_key = Column(String(64), primary_key=True)
    config_value = Column(JSON, nullable=False)
    updated_by = Column(BigInteger)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SysOperLog(Base):
    __tablename__ = "sys_oper_log"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger)
    username = Column(String(64))
    log_type = Column(String(16), nullable=False)      # login / oper
    action = Column(String(255), nullable=False)
    method = Column(String(255))
    ip = Column(String(64))
    user_agent = Column(String(512))
    status = Column(String(32), nullable=False)
    cost_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SemDataSource(Base):
    __tablename__ = "sem_data_source"

    id = Column(BigInteger, primary_key=True)
    group_name = Column(String(64), nullable=False)
    name = Column(String(64), nullable=False)
    object_name = Column(String(128), nullable=False)
    object_type = Column(String(16), nullable=False)
    description = Column(Text)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)


class SemMetric(Base):
    __tablename__ = "sem_metric"

    id = Column(BigInteger, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    expr_sql = Column(Text, nullable=False)
    source_id = Column(BigInteger, nullable=False)
    unit = Column(String(16), nullable=False, default="万元")
    value_type = Column(String(16), nullable=False, default="decimal")
    agg_default = Column(String(16), nullable=False, default="SUM")
    caliber = Column(Text)
    default_format = Column(String(32))
    enabled = Column(Boolean, nullable=False, default=True)


class SemDimension(Base):
    __tablename__ = "sem_dimension"

    id = Column(BigInteger, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(64), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    expr_sql = Column(Text, nullable=False)
    display_expr = Column(Text)
    join_sql = Column(Text)
    source_id = Column(BigInteger, nullable=False)
    dim_type = Column(String(16), nullable=False, default="categorical")
    value_map = Column(JSON)
    enabled = Column(Boolean, nullable=False, default=True)


class SemRule(Base):
    __tablename__ = "sem_rule"

    id = Column(BigInteger, primary_key=True)
    scene = Column(String(64), nullable=False)
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)


class SemFewshot(Base):
    __tablename__ = "sem_fewshot"

    id = Column(BigInteger, primary_key=True)
    question = Column(Text, nullable=False)
    rewritten = Column(Text)
    sql_text = Column(Text, nullable=False)
    source_ids = Column(JSON)
    notes = Column(Text)
    hit_count = Column(Integer, nullable=False, default=0)
    verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SemDictColumn(Base):
    """数据字典：台账列的业务口径（中文名 / 单位 / 维表关联），见 scripts/sql/007。

    台账列若只有英文列名，前端只能给用户看一堆编码；
    ref_* 三个字段描述「编码列去哪张维表换名称」，供台账页 JOIN 使用。
    """

    __tablename__ = "sem_dict_column"

    id = Column(BigInteger, primary_key=True)
    table_name = Column(String(64), nullable=False)
    column_name = Column(String(64), nullable=False)
    cn_name = Column(String(64), nullable=False)
    data_type = Column(String(16), nullable=False, default="text")
    caliber = Column(String(255), nullable=False, default="")
    ref_table = Column(String(64))
    ref_key = Column(String(64))
    ref_label = Column(String(64))
    visible = Column(Boolean, nullable=False, default=True)
    filterable = Column(Boolean, nullable=False, default=True)
    sortable = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)


class ChatSession(Base):
    __tablename__ = "chat_session"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    title = Column(String(128), nullable=False, default="新对话")
    pinned = Column(Boolean, nullable=False, default=False)
    msg_count = Column(Integer, nullable=False, default=0)
    user_feedback = Column(String(32))
    admin_feedback = Column(String(32))
    source_files = Column(JSON, nullable=False, default=list)
    last_msg_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id = Column(BigInteger, primary_key=True)
    session_id = Column(BigInteger, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    payload = Column(JSON)
    rewritten_query = Column(Text)
    intent = Column(String(32))
    model = Column(String(128))
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    cost_ms = Column(Integer)
    trace_id = Column(String(64))
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ChatMessageFeedback(Base):
    __tablename__ = "chat_message_feedback"

    id = Column(BigInteger, primary_key=True)
    message_id = Column(BigInteger, nullable=False)
    session_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    rating = Column(String(16), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ChatQueryTrace(Base):
    __tablename__ = "chat_query_trace"

    id = Column(BigInteger, primary_key=True)
    trace_id = Column(String(64), nullable=False)
    message_id = Column(BigInteger)
    step = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False)
    detail = Column(JSON)
    cost_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class SysModel(Base):
    """模型配置：多场景模型托管，API Key 加密存储。"""

    __tablename__ = "sys_model"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(128), nullable=False)
    provider = Column(String(64), nullable=False)
    base_url = Column(String(255), nullable=False)
    model_name = Column(String(128), nullable=False)
    api_key_enc = Column(Text)
    scene = Column(String(64), nullable=False, default="chat_qa")
    is_default = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    params = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class QuickQuestion(Base):
    """快捷提问：category = recent 常问 / recommend 推荐 / favorite 收藏。"""

    __tablename__ = "quick_question"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger)
    question = Column(String(255), nullable=False)
    category = Column(String(16), nullable=False)
    hit_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class QaFeedback(Base):
    __tablename__ = "qa_feedback"

    id = Column(BigInteger, primary_key=True)
    question = Column(Text, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    username = Column(String(64))
    ai_reply = Column(Text)
    session_id = Column(BigInteger)
    message_id = Column(BigInteger)
    status = Column(String(16), nullable=False, default="待处理")
    remark = Column(Text)
    handled_by = Column(BigInteger)
    handled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
