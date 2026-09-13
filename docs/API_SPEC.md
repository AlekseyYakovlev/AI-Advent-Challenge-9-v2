# API Specification

## Agent (port 8001)
- GET  /health
- GET  /api/v1/chats
- POST /api/v1/chats
- DELETE /api/v1/chats/{chat_id}
- GET  /api/v1/chats/{chat_id}/tree
- POST /api/v1/chats/{chat_id}/branch
- GET  /api/v1/settings?chat_id={id}
- PUT  /api/v1/settings
- WS   /ws/chat/{chat_id}

## LM Studio
- GET  /api/v1/lm-studio/models
- POST /api/v1/lm-studio/load-model
- POST /api/v1/lm-studio/unload-model/{model_id}