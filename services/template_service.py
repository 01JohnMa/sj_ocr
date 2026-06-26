# services/template_service.py
"""模板服务 - 文档模板管理和动态 Prompt 构建"""

import json
import re
from typing import Optional, Dict, Any, List
from loguru import logger

from config.settings import settings
from constants.document_types import DOC_TYPE_TABLE_MAP
from services.base import SupabaseClientMixin, build_field_table, build_examples_section


class TemplateService(SupabaseClientMixin):
    """模板服务封装"""
    
    _instance: Optional['TemplateService'] = None
    _client = None
    
    # Prompt 模板骨架
    EXTRACTION_PROMPT_TEMPLATE = """你是一个专业的数据提取助手，专门处理{doc_type}的OCR识别文本。请从用户提供的文本中精准提取以下字段。

**目标字段：**
{field_list}

**处理规则：**
1. 日期格式统一为 YYYY-MM-DD
2. 缺失字段值设为空字符串 ""
3. 数值保持原文精度，保留单位
4. 确保 JSON 语法正确（使用英文双引号、英文逗号）

{examples_section}

**输出要求：**
- 仅输出扁平的 JSON 对象，只包含上述目标字段，禁止添加任何其他字段
- 不要包含任何解释、引言或 Markdown 代码块标记

现在，请处理用户提供的OCR文本：
{ocr_text}"""
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    # ============ 模板操作 ============
    
    async def get_tenant_templates(
        self, 
        tenant_id: str, 
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取租户的所有模板
        
        Args:
            tenant_id: 租户ID
            active_only: 是否只返回启用的模板
            
        Returns:
            模板列表
        """
        try:
            query = self._get_client().table("document_templates").select(
                "id, tenant_id, name, code, description, required_doc_count, sort_order"
            ).eq("tenant_id", tenant_id)
            
            if active_only:
                query = query.eq("is_active", True)
            
            query = query.order("sort_order").order("name")
            result = await self._run_sync(query.execute)
            
            return result.data or []
        except Exception as e:
            logger.error(f"获取租户模板列表失败: {e}")
            return []
    
    _UUID_RE = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE,
    )

    async def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        获取模板详情（按 UUID 查询）
        
        Args:
            template_id: 模板ID（必须是合法 UUID，否则直接返回 None）
            
        Returns:
            模板信息，template_id 非法 UUID 时返回 None
        """
        if not self._UUID_RE.match(template_id):
            logger.debug(f"get_template: 非 UUID 格式，跳过查询: {template_id!r}")
            return None
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("document_templates").select("*").eq("id", template_id).execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"获取模板失败: {e}")
            return None
    
    # 文档分类结果到模板 code 的映射
    DOC_TYPE_TO_CODE = {
        "检测报告": "inspection_report",
        "快递单": "express",
        "抽样单": "sampling",
        "包装": "packaging",
    }
    
    async def get_template_by_code(
        self, 
        tenant_id: str, 
        code: str
    ) -> Optional[Dict[str, Any]]:
        """
        根据代码或名称获取模板（含字段和示例）
        
        支持按 code、name 或中文分类结果查询
        
        Args:
            tenant_id: 租户ID
            code: 模板代码、名称或分类结果
            
        Returns:
            模板信息（含 fields 和 examples）
        """
        try:
            # 先尝试映射中文分类结果到模板 code
            mapped_code = self.DOC_TYPE_TO_CODE.get(code, code)
            
            # 先按 code 查询
            result = await self._run_sync(
                lambda: self._get_client().table("document_templates").select(
                    "*, template_fields(*), template_examples(*)"
                ).eq("tenant_id", tenant_id).eq("code", mapped_code).execute()
            )

            if not result.data:
                result = await self._run_sync(
                    lambda: self._get_client().table("document_templates").select(
                        "*, template_fields(*), template_examples(*)"
                    ).eq("tenant_id", tenant_id).eq("name", code).execute()
                )
            
            if not result.data:
                logger.warning(f"未找到模板: tenant={tenant_id}, code/name={code}, mapped={mapped_code}")
                return None
            
            template = result.data[0]
            
            # 排序字段
            if template.get("template_fields"):
                template["template_fields"].sort(key=lambda x: x.get("sort_order", 0))
            
            # 排序示例
            if template.get("template_examples"):
                template["template_examples"].sort(key=lambda x: x.get("sort_order", 0))
                # 只保留启用的示例
                template["template_examples"] = [
                    ex for ex in template["template_examples"] 
                    if ex.get("is_active", True)
                ]
            
            return template
        except Exception as e:
            logger.error(f"获取模板失败: {e}")
            return None
    
    async def get_template_with_details(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        获取模板完整信息（含字段、示例）
        
        Args:
            template_id: 模板ID
            
        Returns:
            模板完整信息
            
        Raises:
            Exception: 数据库查询异常（向上抛出，让调用者处理）
        """
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("document_templates").select(
                    "*, template_fields(*), template_examples(*)"
                ).eq("id", template_id).execute()
            )
            
            if not result.data:
                logger.debug(f"模板不存在: {template_id}")
                return None
            
            template = result.data[0]
            
            # 排序字段和示例
            if template.get("template_fields"):
                template["template_fields"].sort(key=lambda x: x.get("sort_order", 0))
            
            if template.get("template_examples"):
                template["template_examples"].sort(key=lambda x: x.get("sort_order", 0))
                template["template_examples"] = [
                    ex for ex in template["template_examples"] 
                    if ex.get("is_active", True)
                ]
            
            return template
        except Exception as e:
            logger.error(f"获取模板详情失败 [{template_id}]: {type(e).__name__}: {e}")
            raise  # 向上抛出，让调用者处理
    
    # ============ 字段操作 ============
    
    async def get_template_fields(self, template_id: str) -> List[Dict[str, Any]]:
        """
        获取模板的字段列表
        
        Args:
            template_id: 模板ID
            
        Returns:
            字段列表
        """
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("template_fields").select(
                    "*"
                ).eq("template_id", template_id).order("sort_order").execute()
            )
            
            return result.data or []
        except Exception as e:
            logger.error(f"获取模板字段失败: {e}")
            return []
    
    # ============ 示例操作 ============
    
    async def get_template_examples(
        self, 
        template_id: str, 
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取模板的 few-shot 示例列表
        
        Args:
            template_id: 模板ID
            active_only: 是否只返回启用的示例
            
        Returns:
            示例列表
        """
        try:
            query = self._get_client().table("template_examples").select(
                "*"
            ).eq("template_id", template_id)
            
            if active_only:
                query = query.eq("is_active", True)
            
            query = query.order("sort_order")
            result = await self._run_sync(query.execute)
            
            return result.data or []
        except Exception as e:
            logger.error(f"获取模板示例失败: {e}")
            return []
    
    # ============ 动态 Prompt 构建 ============
    
    def build_extraction_prompt(
        self, 
        template: Dict[str, Any], 
        ocr_text: str
    ) -> str:
        """
        根据模板配置动态构建 LLM 提取 Prompt
        
        Args:
            template: 模板信息（含 template_fields 和 template_examples）
            ocr_text: OCR 识别文本
            
        Returns:
            构建好的 Prompt
        """
        custom_prompt = template.get("extraction_prompt_template")
        if custom_prompt:
            return custom_prompt.replace("{ocr_text}", ocr_text)

        # 1. 构建字段列表
        fields = template.get("template_fields", [])
        field_list = build_field_table(fields)
        
        # 2. 构建示例部分
        examples = template.get("template_examples", [])
        examples_section = build_examples_section(examples)
        
        # 3. 组装完整 Prompt
        prompt = self.EXTRACTION_PROMPT_TEMPLATE.format(
            doc_type=template.get("name", "文档"),
            field_list=field_list,
            examples_section=examples_section,
            ocr_text=ocr_text
        )
        
        return prompt
    
    def build_field_mapping(self, template: Dict[str, Any]) -> Dict[str, str]:
        """
        构建字段到飞书列名的映射
        
        Args:
            template: 模板信息（含 template_fields）
            
        Returns:
            {field_key: feishu_column} 映射
        """
        fields = template.get("template_fields", [])
        mapping = {}
        
        for field in fields:
            field_key = field.get("field_key")
            feishu_column = field.get("feishu_column")
            
            if field_key and feishu_column:
                mapping[field_key] = feishu_column
        
        return mapping
    
    def get_field_keys(self, template: Dict[str, Any]) -> List[str]:
        """
        获取模板的所有字段键名列表
        
        Args:
            template: 模板信息
            
        Returns:
            字段键名列表
        """
        fields = template.get("template_fields", [])
        return [f.get("field_key") for f in fields if f.get("field_key")]

    # ============ 管理员 CRUD：字段操作 ============

    async def create_template(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """创建文档模板基本信息。"""
        allowed_keys = {
            "tenant_id", "name", "code", "description", "required_doc_count",
            "extraction_mode", "per_page_extraction", "extraction_prompt_template",
            "cleaner_module", "output_mode", "excel_template_file_name",
            "excel_template_path", "excel_template_placeholders",
            "is_active", "sort_order",
        }
        payload = {k: v for k, v in data.items() if k in allowed_keys}
        if "is_active" not in payload:
            payload["is_active"] = True
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("document_templates").insert(payload).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"创建模板失败: {e}")
            raise

    async def _get_result_table_for_template(self, template_id: str) -> Optional[str]:
        """
        根据模板 ID 查出模板 code，再映射到对应的结果表名。

        若模板 code 不在 DOC_TYPE_TABLE_MAP 中（如自定义模板），返回 None，
        调用方跳过 DDL 同步但不报错。
        """
        template = await self.get_template(template_id)
        if not template:
            return None
        code = template.get("code", "")
        table = DOC_TYPE_TABLE_MAP.get(code)
        if not table:
            logger.debug(f"模板 code={code!r} 无对应结果表，跳过 DDL 同步")
        return table

    async def create_field(self, template_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        新增模板字段，并在对应结果表中自动 ADD COLUMN。

        先执行 DDL（失败直接抛 SchemaError 阻止配置写入），
        再写 template_fields，保持两者一致性。
        """
        from services.schema_sync_service import schema_sync_service, SchemaError

        field_key = data.get("field_key", "")

        # 1. 先同步数据库列（DDL 失败则阻止后续配置写入）
        if field_key:
            table_name = await self._get_result_table_for_template(template_id)
            if table_name:
                await schema_sync_service.add_column(table_name, field_key)
                # SchemaError 向上传播 -> 前端收到 422

        # 2. DDL 成功后写模板字段配置
        try:
            payload = {
                "template_id": template_id,
                "field_key": field_key,
                "field_label": data["field_label"],
                "field_type": data.get("field_type", "text"),
                "extraction_hint": data.get("extraction_hint", ""),
                "feishu_column": data.get("feishu_column", ""),
                "sort_order": data.get("sort_order", 0),
                "review_enforced": data.get("review_enforced", False),
                "review_allowed_values": data.get("review_allowed_values", None),
            }
            result = await self._run_sync(
                lambda: self._get_client().table("template_fields").insert(payload).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"新增模板字段失败: {e}")
            raise

    async def update_field(self, field_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        更新模板字段。

        若 field_key 发生变更（改名），先执行 RENAME COLUMN，
        再更新 template_fields 记录。
        """
        from services.schema_sync_service import schema_sync_service, SchemaError

        # 获取旧字段信息（用于检测 field_key 是否改变）
        old_field = await self.get_field_by_id(field_id)
        if not old_field:
            raise ValueError("字段不存在")

        old_key = old_field.get("field_key", "")
        new_key = data.get("field_key", old_key)

        # 检测 field_key 是否改变，若改变则先执行列改名
        if new_key and new_key != old_key:
            template_id = old_field.get("template_id", "")
            table_name = await self._get_result_table_for_template(template_id)
            if table_name:
                await schema_sync_service.rename_column(table_name, old_key, new_key)
                # SchemaError 向上传播

        try:
            allowed_keys = {
                "field_key", "field_label", "field_type", "extraction_hint",
                "feishu_column", "sort_order", "review_enforced", "review_allowed_values",
            }
            payload = {k: v for k, v in data.items() if k in allowed_keys}
            result = await self._run_sync(
                lambda: self._get_client().table("template_fields").update(payload).eq("id", field_id).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"更新模板字段失败: {e}")
            raise

    async def delete_field(self, field_id: str, force: bool = False) -> bool:
        """
        删除模板字段，并在对应结果表中自动 DROP COLUMN。

        先执行 DDL（有历史数据且 force=False 时抛 SchemaError），
        再删除 template_fields 记录，保持两者一致性。

        Args:
            field_id: 字段 ID
            force: True 时即使有历史数据也强制删列（数据永久丢失）
        """
        from services.schema_sync_service import schema_sync_service, SchemaError

        # 获取字段信息
        field = await self.get_field_by_id(field_id)
        if not field:
            return True  # 已不存在，视为成功

        field_key = field.get("field_key", "")
        template_id = field.get("template_id", "")

        # 1. 先执行列删除（有数据且非 force 时 SchemaError 阻止后续操作）
        if field_key:
            table_name = await self._get_result_table_for_template(template_id)
            if table_name:
                await schema_sync_service.drop_column(table_name, field_key, force=force)

        # 2. 列删除成功后再删除字段配置
        try:
            await self._run_sync(
                lambda: self._get_client().table("template_fields").delete().eq("id", field_id).execute()
            )
            return True
        except Exception as e:
            logger.error(f"删除模板字段失败: {e}")
            raise

    async def reorder_fields(self, template_id: str, order_list: List[Dict[str, Any]]) -> bool:
        """
        批量更新字段排序
        
        Args:
            template_id: 模板ID（用于校验）
            order_list: [{"id": field_id, "sort_order": int}, ...]
        """
        try:
            for item in order_list:
                await self._run_sync(
                    lambda item=item: self._get_client().table("template_fields").update(
                        {"sort_order": item["sort_order"]}
                    ).eq("id", item["id"]).eq("template_id", template_id).execute()
                )
            return True
        except Exception as e:
            logger.error(f"批量排序字段失败: {e}")
            raise

    async def get_field_by_id(self, field_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取单个字段（含 template_id，供权限校验用）"""
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("template_fields").select("*").eq("id", field_id).execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"获取字段失败: {e}")
            return None

    # ============ 管理员 CRUD：示例操作 ============

    async def create_example(self, template_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """新增 few-shot 示例"""
        try:
            payload = {
                "template_id": template_id,
                "example_input": data.get("example_input", ""),
                "example_output": data.get("example_output", {}),
                "sort_order": data.get("sort_order", 0),
                "is_active": data.get("is_active", True),
            }
            result = await self._run_sync(
                lambda: self._get_client().table("template_examples").insert(payload).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"新增示例失败: {e}")
            raise

    async def update_example(self, example_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """更新 few-shot 示例"""
        try:
            allowed_keys = {"example_input", "example_output", "sort_order", "is_active"}
            payload = {k: v for k, v in data.items() if k in allowed_keys}
            result = await self._run_sync(
                lambda: self._get_client().table("template_examples").update(payload).eq("id", example_id).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"更新示例失败: {e}")
            raise

    async def delete_example(self, example_id: str) -> bool:
        """删除 few-shot 示例"""
        try:
            await self._run_sync(
                lambda: self._get_client().table("template_examples").delete().eq("id", example_id).execute()
            )
            return True
        except Exception as e:
            logger.error(f"删除示例失败: {e}")
            raise

    async def get_example_by_id(self, example_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取单个示例（含 template_id，供权限校验用）"""
        try:
            result = await self._run_sync(
                lambda: self._get_client().table("template_examples").select("*").eq("id", example_id).execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"获取示例失败: {e}")
            return None

    # ============ 管理员 CRUD：模板基本信息操作 ============

    async def get_admin_templates(
        self,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取可管理的模板列表（含字段和示例数量）
        
        Args:
            tenant_id: 指定租户ID（super_admin 按此过滤，None 则返回全部）
        """
        try:
            query = self._get_client().table("document_templates").select(
                "id, tenant_id, name, code, description, "
                "required_doc_count, sort_order, is_active, auto_approve, "
                "push_attachment, extraction_mode, per_page_extraction, "
                "extraction_prompt_template, cleaner_module, "
                "output_mode, excel_template_file_name, excel_template_path, "
                "excel_template_placeholders, "
                "feishu_bitable_token, feishu_table_id"
            )
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            query = query.order("sort_order").order("name")
            result = await self._run_sync(query.execute)
            return result.data or []
        except Exception as e:
            logger.error(f"获取管理模板列表失败: {e}")
            return []

    async def update_template_config(self, template_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        更新模板配置（飞书推送、自动审批、提取引擎等）
        
        Args:
            template_id: 模板ID
            data: 包含 feishu_bitable_token, feishu_table_id, auto_approve, extraction_mode 的字典
        """
        try:
            allowed_keys = {
                "feishu_bitable_token", "feishu_table_id", "auto_approve",
                "extraction_mode", "push_attachment", "per_page_extraction",
                "output_mode", "excel_template_file_name", "excel_template_path",
                "excel_template_placeholders",
            }
            payload = {k: v for k, v in data.items() if k in allowed_keys}
            if not payload:
                return {}

            # 先更新，再显式回读，避免 PostgREST 返回最小化响应导致前端拿到空对象
            await self._run_sync(
                lambda: self._get_client().table("document_templates").update(payload).eq("id", template_id).execute()
            )
            fresh = await self._run_sync(
                lambda: self._get_client().table("document_templates").select(
                    "id, tenant_id, name, code, description, required_doc_count, "
                    "sort_order, is_active, auto_approve, push_attachment, extraction_mode, per_page_extraction, "
                    "extraction_prompt_template, cleaner_module, "
                    "output_mode, excel_template_file_name, excel_template_path, "
                    "excel_template_placeholders, "
                    "feishu_bitable_token, feishu_table_id"
                ).eq("id", template_id).execute()
            )
            return fresh.data[0] if fresh.data else {}
        except Exception as e:
            logger.error("更新模板配置失败: {}", e)
            raise
    
    # ============ Merge 模式支持 ============
    
    def merge_extraction_results(
        self, 
        result_a: Optional[Dict[str, Any]], 
        result_b: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        合并两份文档的提取结果
        
        Args:
            result_a: 文档A的提取结果
            result_b: 文档B的提取结果
            
        Returns:
            合并后的结果
        """
        merged = {}
        
        if result_a:
            merged.update(result_a)
        
        if result_b:
            merged.update(result_b)
        
        return merged


# 单例实例
template_service = TemplateService()
