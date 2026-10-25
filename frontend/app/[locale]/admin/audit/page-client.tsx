"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ScrollText } from "lucide-react";
import { Button } from "~/ui/primitives/button";
import { Card, CardContent } from "~/ui/primitives/card";
import { Input } from "~/ui/primitives/input";
import { Label } from "~/ui/primitives/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/ui/primitives/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/ui/primitives/table";
import { AdminPageHeader, EmptyState, TableSkeleton } from "../components";
import { ErrorAlert } from "~/ui/components/error-alert";
import { Pagination } from "~/ui/components/pagination";
import { useApiGet } from "~/lib/hooks/use-api";
import { useDraftFilters } from "~/lib/hooks/use-draft-filters";
import { auditApi } from "~/lib/api/admin-api";

const PAGE_SIZE = 50;

export function AuditClient() {
  const t = useTranslations("audit");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const { draft, applied, set, apply, reset } = useDraftFilters({ user_id: "", event_type: "all", routing_key: "", from: "", to: "" });

  const query = {
    page,
    user_id: applied.user_id.trim() || undefined,
    event_type: applied.event_type === "all" ? undefined : applied.event_type,
    routing_key: applied.routing_key.trim() || undefined,
    from: applied.from || undefined,
    // The whole "to" day counts, not just its first moment
    to: applied.to ? `${applied.to}T23:59:59.999` : undefined,
  };
  const { data, error, isLoading } = useApiGet(`/audit/logs?${JSON.stringify(query)}`, () => auditApi.list(query));
  const rows = data?.results ?? [];

  const fmt = (iso: string) =>
    new Date(iso).toLocaleString(locale === "en" ? "en-US" : "uk-UA", { dateStyle: "short", timeStyle: "medium" });
  const onApply = () => { setPage(1); apply(); };
  const onReset = () => { setPage(1); reset(); };

  return (
    <div className="min-h-dvh bg-muted/50 p-4 sm:p-8">
      <div className="max-w-7xl mx-auto">
        <AdminPageHeader title={t("title")} subtitle={t("subtitle")} icon={ScrollText} backLabel={tc("back")} />

        <form className="flex flex-wrap items-end gap-4 mb-4" onSubmit={(e) => { e.preventDefault(); onApply(); }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-user" className="text-xs text-muted-foreground">{t("userId")}</Label>
            <Input id="audit-user" value={draft.user_id} onChange={(e) => set("user_id", e.target.value)} className="w-56" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground">{t("type")}</Label>
            <Select value={draft.event_type} onValueChange={(v) => set("event_type", v)}>
              <SelectTrigger className="w-40" aria-label={t("type")}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allTypes")}</SelectItem>
                <SelectItem value="http_request">{t("type_http_request")}</SelectItem>
                <SelectItem value="rabbitmq">{t("type_rabbitmq")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-action" className="text-xs text-muted-foreground">{t("action")}</Label>
            <Input id="audit-action" value={draft.routing_key} placeholder={t("actionPlaceholder")}
              onChange={(e) => set("routing_key", e.target.value)} className="w-48" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-from" className="text-xs text-muted-foreground">{t("from")}</Label>
            <Input id="audit-from" type="date" value={draft.from} onChange={(e) => set("from", e.target.value)} className="w-40" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-to" className="text-xs text-muted-foreground">{t("to")}</Label>
            <Input id="audit-to" type="date" value={draft.to} min={draft.from || undefined}
              onChange={(e) => set("to", e.target.value)} className="w-40" />
          </div>
          <Button type="submit" size="sm">{tc("apply")}</Button>
          <Button type="button" size="sm" variant="outline" onClick={onReset}>{tc("reset")}</Button>
        </form>

        <ErrorAlert message={error?.message ?? null} />

        {isLoading && !data ? (
          <TableSkeleton rows={8} cols={6} />
        ) : (
          <Card className="dark:bg-card dark:border-border">
            <CardContent className="pt-6">
              <div className="border rounded-lg dark:border-border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/50 border-b dark:border-border">
                      <TableHead>{t("date")}</TableHead>
                      <TableHead>{t("service")}</TableHead>
                      <TableHead>{t("type")}</TableHead>
                      <TableHead>{t("action")}</TableHead>
                      <TableHead>{t("user")}</TableHead>
                      <TableHead className="text-right">{t("status")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.length === 0 ? (
                      <EmptyState icon={ScrollText} message={t("empty")} colSpan={6} />
                    ) : (
                      rows.map((r) => (
                        <TableRow key={r.id}>
                          <TableCell className="whitespace-nowrap text-muted-foreground">{fmt(r.created_at)}</TableCell>
                          <TableCell>{r.service}</TableCell>
                          <TableCell>{t(`type_${r.event_type}`)}</TableCell>
                          <TableCell className="font-mono text-xs break-all">
                            {r.routing_key ?? `${r.method ?? ""} ${r.path ?? ""}`.trim()}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{r.user_id || "—"}</TableCell>
                          <TableCell className="text-right tabular-nums">{r.status_code ?? "—"}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}

        <Pagination currentPage={page} totalPages={Math.ceil((data?.total ?? 0) / PAGE_SIZE)} loading={isLoading} onPageChange={setPage} />
      </div>
    </div>
  );
}
