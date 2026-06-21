# 원격 접속 경계 — Tailscale serve (REMOTE_ACCESS_PLAN Phase B)

천문대 제어 PC와 운영자 기기를 한 tailnet으로 묶고, `tailscale serve`로 ASTERION을
**tailnet 내부에만 비공개 HTTPS**로 노출한다. 공개 인터넷 노출 0, 포트포워딩·도메인·
Let's Encrypt 불필요, CGNAT 자동 우회. (Funnel = 공개 노출이라 **사용 금지**.)

```
운영자 기기(브라우저, tailnet 가입)  ──HTTPS(비공개)──►  tailscale serve  ──localhost──►  ASTERION :8520
```

## 1. 설치 · 로그인 (한 번)

- 천문대 PC + 각 운영자 기기에 Tailscale 설치 후 **같은 tailnet**으로 로그인.
- 무료 Personal: 6 사용자 · 기기 무제한.
- MagicDNS 켜기(Admin console → DNS) → 천문대 PC 이름이 `asterion-pc.<tailnet>.ts.net` 형태로.

## 2. ASTERION을 tailnet에만 노출 (천문대 PC에서)

ASTERION은 평소대로 localhost에 띄운다(`python -m asterion`, 기본 127.0.0.1:8520).
그 위에 serve로 비공개 HTTPS를 건다:

```
tailscale serve --bg --https=443 http://127.0.0.1:8520
tailscale serve status     # https://asterion-pc.<tailnet>.ts.net 확인
```

- 이제 tailnet 안의 기기에서 `https://asterion-pc.<tailnet>.ts.net` 로 접속(인증서 자동).
- **앱은 계속 127.0.0.1 바인딩 유지** — `server.host=0.0.0.0` 로 바꾸지 말 것(노출 불변식).
- **Funnel 금지**: `tailscale funnel` 은 공개 인터넷 노출이라 쓰지 않는다. serve(비공개)만.

해제: `tailscale serve --https=443 off`

## 3. ACL — 누가 천문대 노드에 닿나 (Admin console → Access controls)

기본은 tailnet 전체 허용. 운영자만 천문대 노드(8520/443)에 닿게 좁히려면 태그/그룹으로:

```jsonc
{
  "groups": { "group:operators": ["alice@github", "bob@gmail.com"] },
  "tagOwners": { "tag:observatory": ["group:operators"] },
  "acls": [
    { "action": "accept", "src": ["group:operators"], "dst": ["tag:observatory:443"] }
  ]
}
```

천문대 PC를 `tag:observatory` 로 태깅(`tailscale up --advertise-tags=tag:observatory`).

## 4. 앱 하드닝 (config.local.json)

serve 뒤에서는 HTTPS이므로 다음을 권장:

```jsonc
{
  "server": {
    "forwarded_allow_ips": "127.0.0.1",                 // serve는 localhost에서 프록시
    "allowed_hosts": ["asterion-pc.<tailnet>.ts.net", "127.0.0.1", "localhost"],
    "auth": {
      "enabled": true,
      "cookie_secure": true,                            // HTTPS 전용 쿠키
      "session_secret": "<python -m asterion.access secret>",
      "users": { "alice": { "role": "admin", "password": "pbkdf2_sha256$..." } }
    }
  }
}
```

### (선택) 비번 없이 tailnet 신원으로 로그인

serve는 요청에 `Tailscale-User-Login`(검증된 tailnet 사용자)을 주입한다. 이를 신뢰해
비번 단계를 생략하려면:

```jsonc
"auth": {
  "enabled": true,
  "trust_tailscale_identity": true,                     // ⚠ serve 뒤에서만! 직접 노출 시 위조 가능
  "tailscale_users": { "alice@github": "admin", "bob@gmail.com": "operator" }
}
```

> ⚠ `trust_tailscale_identity` 는 **serve/신뢰 프록시 뒤에서만** 켤 것. 앱을 직접 노출한
> 상태로 켜면 클라이언트가 헤더를 위조할 수 있다(그래서 기본 off).

## 5. 점검 체크리스트

- [ ] `server.host` 가 여전히 `127.0.0.1` (공개 0.0.0.0 아님)
- [ ] `tailscale serve status` 가 https 비공개로 8520 프록시
- [ ] `tailscale funnel status` 가 **꺼짐**(공개 노출 없음)
- [ ] tailnet **밖** 기기에선 접속 불가, 안에서만 가능
- [ ] auth.enabled=true + cookie_secure=true + (선택) ACL 로 운영자만 도달
