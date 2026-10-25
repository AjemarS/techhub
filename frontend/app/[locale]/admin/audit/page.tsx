import { Suspense } from "react";
import { AuditClient } from "./page-client";
import AdminAuditLoading from "./loading";

export default function AuditPage() {
  return (
    <Suspense fallback={<AdminAuditLoading />}>
      <AuditClient />
    </Suspense>
  );
}
