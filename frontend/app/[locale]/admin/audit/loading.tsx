import { TableSkeleton } from "../components/loading-skeleton";
import { Skeleton } from "~/ui/primitives/skeleton";

export default function AdminAuditLoading() {
  return (
    <div className="space-y-6 p-4 sm:p-8">
      <Skeleton className="h-8 w-48" />
      <TableSkeleton rows={8} cols={6} />
    </div>
  );
}
