"""Fail closed when native large-tool-result persistence cannot complete.

Deep Agents owns thresholds, previews, storage and pagination guidance. The two
private processing hooks are compatibility seams, covered by contract tests.
"""
from deepagents.middleware.filesystem import FilesystemMiddleware, NUM_CHARS_PER_TOKEN
from deepagents.middleware._message_eviction import _extract_text_from_message


class ToolOutputPersistenceError(RuntimeError):
    code = 'tool_output_persistence_failed'

    def __init__(self, message, reason='backend_write_failed'):
        # Never echo tool output or backend exceptions (which can include content).
        self.tool_call_id = str(message.tool_call_id)[:120]
        self.tool_name = str(message.name or '')[:120]
        super().__init__(f'{self.code}: tool={self.tool_name} call={self.tool_call_id} reason={reason}; '
                         '工具已執行，但大型輸出保存失敗；本次執行已停止，未自動重跑工具。')


class SafeFilesystemMiddleware(FilesystemMiddleware):
    @property
    def name(self):
        # Replace the framework slot; also inherited by its general-purpose agent.
        return 'FilesystemMiddleware'

    def _requires_offload(self, message):
        limit = self._tool_token_limit_before_evict
        return bool(limit and len(_extract_text_from_message(message)) > NUM_CHARS_PER_TOKEN * limit)

    def _process_large_message(self, message, resolved_backend):
        required = self._requires_offload(message)
        try:
            result = super()._process_large_message(message, resolved_backend)
        except Exception as exc:
            if required:
                raise ToolOutputPersistenceError(message, type(exc).__name__) from None
            raise
        if required and not result[1]:
            raise ToolOutputPersistenceError(message)
        return result

    async def _aprocess_large_message(self, message, resolved_backend):
        required = self._requires_offload(message)
        try:
            result = await super()._aprocess_large_message(message, resolved_backend)
        except Exception as exc:
            if required:
                raise ToolOutputPersistenceError(message, type(exc).__name__) from None
            raise
        if required and not result[1]:
            raise ToolOutputPersistenceError(message)
        return result
