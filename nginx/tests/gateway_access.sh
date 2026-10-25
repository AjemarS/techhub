#!/bin/sh
# Gateway access checks against a running stack (FIXES items 5, 6, 7, 9).
# Usage: sh nginx/tests/gateway_access.sh   (GATEWAY, ADMIN_EMAIL, ADMIN_PASSWORD, CORS_TEST_ORIGIN, INTERNAL_SERVICE_TOKEN optional)
G=${GATEWAY:-http://localhost}
fail=0

check() { # name "expected codes" method path [curl args...]
  name=$1; want=$2; m=$3; p=$4; shift 4
  code=$(curl -s -o /dev/null -w '%{http_code}' -X "$m" "$@" "$G$p")
  case " $want " in
    *" $code "*) echo "ok   $name ($code)" ;;
    *) echo "FAIL $name: got $code, want $want"; fail=1 ;;
  esac
}

limited() { # n method path [curl args...] -> how many of n requests were rate limited (429, or nginx's default 503)
  n=$1; m=$2; p=$3; shift 3; hits=0; i=0
  while [ $i -lt "$n" ]; do
    case $(curl -s -o /dev/null -w '%{http_code}' -X "$m" "$@" "$G$p") in 429|503) hits=$((hits + 1)) ;; esac
    i=$((i + 1))
  done
  echo $hits
}

echo "# 5. audit log"
check "anon GET /api/audit/logs" "401 403" GET /api/audit/logs
check "anon POST /api/audit/log" "401 403 404" POST /api/audit/log -H "Content-Type: application/json" -d '{"event_type":"forged"}'
check "anon GET /api/audit/logs with forged role header" "401 403" GET /api/audit/logs -H "X-Gateway-User-Id: x" -H "X-Gateway-User-Role: admin"
if [ -n "$ADMIN_EMAIL" ]; then
  jar=$(mktemp)
  curl -s -o /dev/null -c "$jar" -H "Content-Type: application/json" -H "Origin: $G" \
    -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" "$G/auth/sign-in/email"
  check "admin GET /api/audit/logs" "200" GET /api/audit/logs -b "$jar"
  check "admin POST /api/audit/log" "401 403 404" POST /api/audit/log -b "$jar" -H "Content-Type: application/json" -d '{}'
  rm -f "$jar"
else
  echo "skip admin checks (set ADMIN_EMAIL / ADMIN_PASSWORD)"
fi

echo "# 6. inventory reads"
for p in /api/inventory/suppliers/ /api/inventory/goods-receipts/ /api/inventory/stock/movements/ \
         "/api/inventory/goods-receipts/product_info/?product_id=1&warehouse_id=1"; do
  check "anon GET $p" "401 403" GET "$p"
done
check "anon GET /api/inventory/stock/ (storefront availability)" "200" GET /api/inventory/stock/
check "anon GET /api/inventory/warehouses/ (checkout showrooms)" "200" GET /api/inventory/warehouses/

echo "# 7. rate limit only on online order creation"
n=$(limited 30 GET /api/orders/my/)
[ "$n" = 0 ] && echo "ok   30x GET /api/orders/my/ not limited" || { echo "FAIL 30x GET /api/orders/my/: $n limited"; fail=1; }
n=$(limited 15 POST /api/orders/pos/ -H "Content-Type: application/json" -d '{}')
[ "$n" = 0 ] && echo "ok   15x POST /api/orders/pos/ not limited" || { echo "FAIL 15x POST /api/orders/pos/: $n limited"; fail=1; }
codes=""; i=0
while [ $i -lt 20 ]; do
  codes="$codes $(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" -d '{}' "$G/api/orders/")"
  i=$((i + 1))
done
case "$codes" in
  *" 429"*) echo "ok   20x POST /api/orders/ limited with 429" ;;
  *" 503"*) echo "FAIL 20x POST /api/orders/ limited with 503, want 429"; fail=1 ;;
  *) echo "FAIL 20x POST /api/orders/: never limited"; fail=1 ;;
esac

echo "# 8a. paying: guests reach order-service, which wants the order's pay_token"
check "anon POST pay without token" "404" POST /api/orders/999999/pay/ -H "Content-Type: application/json" -d '{}'

echo "# 8b. reviews: signed-in writes only, product moderation stays admin"
check "anon POST review" "401" POST /api/products/1/reviews/ -H "Content-Type: application/json" -d '{"rating":5}'
check "anon PATCH review moderation" "401" PATCH /api/reviews/1/ -H "Content-Type: application/json" -d '{"is_published":false}'
check "anon GET reviews" "200 404" GET /api/products/1/reviews/

echo "# 9. CORS_ORIGINS reaches njs"
O=${CORS_TEST_ORIGIN:-http://localhost:3000}
case "$O" in http://localhost*|https://localhost*) echo "note: $O is always allowed; set CORS_TEST_ORIGIN to a non-localhost entry of CORS_ORIGINS" ;; esac
acao=$(curl -s -o /dev/null -D - -H "Origin: $O" "$G/gateway" | tr -d '\r' | sed -n 's/^[Aa]ccess-[Cc]ontrol-[Aa]llow-[Oo]rigin: //p')
[ "$acao" = "$O" ] && echo "ok   Origin $O allowed" || { echo "FAIL Origin $O: Access-Control-Allow-Origin='$acao'"; fail=1; }

echo "# 10. CSP on storefront HTML"
hdrs=$(curl -s -o /dev/null -D - "$G/ua" | tr -d '\r')
csp=$(echo "$hdrs" | grep -i '^content-security-policy:')
script_src=$(echo "$csp" | tr ';' '\n' | grep "script-src")
[ "$(echo "$csp" | grep -c .)" = 1 ] && echo "ok   one CSP header" || { echo "FAIL expected one CSP header, got: $csp"; fail=1; }
case "$script_src" in
  *"'nonce-"*) case "$script_src" in
    *"'unsafe-inline'"*) echo "FAIL script-src still allows 'unsafe-inline'"; fail=1 ;;
    *) echo "ok   script-src uses a nonce, no 'unsafe-inline'" ;;
  esac ;;
  *) echo "FAIL script-src has no nonce: $script_src"; fail=1 ;;
esac
echo "$hdrs" | grep -qi '^x-xss-protection:' && { echo "FAIL X-XSS-Protection still sent"; fail=1; } || echo "ok   no X-XSS-Protection"

echo "# 33. the service token is not accepted from clients"
check "client X-Internal-Token on stock reserve" "401 403" POST /api/inventory/stock/reserve/   -H "Content-Type: application/json" -H "X-Internal-Token: ${INTERNAL_SERVICE_TOKEN:-x}"   -d '{"product_id":1,"warehouse_id":1,"quantity":1}'

exit $fail
