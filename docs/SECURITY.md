# 🔐 Security & Authentication

## Overview

When `TELEGRAM_VALIDATION_ENABLED=true`, the application uses a two-layer authentication system:

1. **Telegram HMAC-SHA256 Validation** - Initial authentication
2. **JWT Tokens** - Subsequent API requests

---

## Authentication Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User opens Telegram Mini App                             │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend: window.Telegram.WebApp.initData available     │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. POST /api/validate-init (public endpoint)               │
│    Body: { init_data: "..." }                              │
│    - Validates HMAC signature                              │
│    - Checks for replay attacks (Redis)                     │
│    - Issues JWT tokens                                     │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Frontend stores tokens in sessionStorage                │
│    - access_token (15 min TTL)                             │
│    - refresh_token (7 days TTL)                            │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. All subsequent API requests include:                    │
│    Authorization: Bearer <access_token>                    │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. JWT Middleware validates token                          │
│    - Checks signature                                      │
│    - Checks expiration                                     │
│    - Attaches user data to request                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Protected vs Public Endpoints

### Public Endpoints (No Auth)

| Endpoint | Purpose |
|----------|---------|
| `/health*` | Health checks |
| `/api/validation-config` | Get validation mode (dev/prod) |
| `/api/validate-init` | Exchange initData for JWT tokens |
| `/api/auth/refresh` | Refresh access token |

### Protected Endpoints (JWT Required)

**ALL other endpoints require valid JWT:**

- `/api/config` - Client configuration
- `/api/cache/*` - Cache management
- `/api/events*` - Events data
- `/api/streets` - Streets data
- `/api/location*` - Location search
- `/api/data_status` - Data status
- `/ws` - WebSocket (auth via message)

---

## JWT Token Details

### Access Token
- **TTL:** 15 minutes (900 seconds)
- **Usage:** All API requests
- **Storage:** sessionStorage
- **Format:** `Authorization: Bearer <token>`

### Refresh Token
- **TTL:** 7 days (604800 seconds)
- **Usage:** Get new access token
- **Storage:** sessionStorage
- **Endpoint:** `POST /api/auth/refresh`

### Token Payload
```json
{
  "sub": "123456789",           // User ID
  "first_name": "John",
  "username": "john_doe",
  "iat": 1234567890,            // Issued at
  "exp": 1234568790,            // Expiration
  "type": "access"              // Token type
}
```

---

## Development Mode

When `TELEGRAM_VALIDATION_ENABLED=false`:

- **All endpoints are accessible without authentication**
- Test user is automatically attached to requests:
  ```json
  {
    "id": 123456789,
    "first_name": "Dev",
    "username": "devuser"
  }
  ```

**⚠️ Never use dev mode in production!**

---

## Security Features

### 1. HMAC-SHA256 Validation
- Validates Telegram initData signature
- Uses bot token as secret key
- Prevents tampering

### 2. Replay Attack Protection
- Redis-based hash tracking
- Each initData can only be used once
- TTL: 24 hours

### 3. Rate Limiting
- Per-IP limiting
- Default: 60 requests/minute
- Endpoint-specific limits available

### 4. Token Expiration
- Short-lived access tokens (15 min)
- Automatic refresh via token-manager.js
- Expired tokens rejected

### 5. Secure Token Storage
- Tokens stored in sessionStorage (not localStorage)
- Cleared on browser close
- Not accessible across domains

---

## WebSocket Authentication

WebSocket uses a different authentication flow:

```javascript
// 1. Connect without auth
const ws = new WebSocket('ws://localhost/ws');

// 2. Send auth message after connection
ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'auth',
    token_type: 'bearer',
    token: sessionStorage.getItem('access_token')
  }));
};
```

---

## Error Responses

### 401 Unauthorized
```json
{
  "error": "Authentication required",
  "code": "UNAUTHORIZED"
}
```

### 401 Token Invalid
```json
{
  "error": "Invalid or expired token",
  "code": "TOKEN_INVALID"
}
```

---

## Best Practices

### Frontend
1. ✅ Always check for token before API calls
2. ✅ Use token-manager.js for auto-refresh
3. ✅ Handle 401 errors (redirect to login)
4. ✅ Clear tokens on logout

### Backend
1. ✅ Always validate JWT in middleware
2. ✅ Use HTTPS in production
3. ✅ Rotate JWT_SECRET regularly
4. ✅ Monitor failed auth attempts

---

## Testing

### Get JWT Token
```bash
curl -X POST http://localhost:8080/api/validate-init \
  -H "Content-Type: application/json" \
  -d '{"init_data": "query_string_from_telegram"}'
```

### Access Protected Endpoint
```bash
curl http://localhost:8080/api/events \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Refresh Token
```bash
curl -X POST http://localhost:8080/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "YOUR_REFRESH_TOKEN"}'
```

---

## Troubleshooting

### "Authentication required" error
**Cause:** No token provided  
**Solution:** Call `/api/validate-init` first

### "Invalid or expired token" error
**Cause:** Token expired or invalid  
**Solution:** Use `/api/auth/refresh` or re-authenticate

### Can't access `/api/config`
**Cause:** Config now requires auth  
**Solution:** Authenticate first, then access config

---

*Last updated: 2026-03-13*
