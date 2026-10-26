/**
 * Catalog reads made while rendering on the server. They reach the API from this container for every
 * visitor, so through the gateway they would all share one client's rate limit. With CATALOG_SERVICE_URL
 * and INTERNAL_SERVICE_TOKEN set they go straight to product-service, which does not throttle the token.
 * Server-only: neither variable is NEXT_PUBLIC, so the token never reaches the browser bundle.
 */
const GATEWAY_URL = process.env.API_SERVICE_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost/api";

export function catalogFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const catalogUrl = process.env.CATALOG_SERVICE_URL;
  const token = process.env.INTERNAL_SERVICE_TOKEN;
  if (catalogUrl && token) {
    return fetch(`${catalogUrl}${path}`, {
      ...init,
      headers: { ...(init.headers as Record<string, string> | undefined), "X-Internal-Token": token },
    });
  }
  return fetch(`${GATEWAY_URL}${path}`, init);
}
