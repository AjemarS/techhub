"use client";

import { Link, usePathname } from "~/i18n/navigation";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  Users, Package, LayoutDashboard, LogOut, Store, Warehouse,
  ShoppingCart, BarChart3,
  ExternalLink, Pin, PinOff,
  ChevronDown,
  RotateCcw,
  ScrollText,
  Tag,
} from "lucide-react";
import { cn } from "~/lib/cn";
import { Button } from "~/ui/primitives/button";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "~/ui/primitives/collapsible";
import { useCurrentUser } from "~/lib/auth-client";
import { authClient } from "~/lib/auth-client";
import { useRouter } from "~/i18n/navigation";

interface NavChild {
  key: string;
  href: string;
}

interface NavGroup {
  key: string;
  href?: string;
  icon: typeof LayoutDashboard;
  roles: string[];
  children?: NavChild[];
}

const navigationGroups: NavGroup[] = [
  { key: "dashboard", href: "/admin/summary", icon: LayoutDashboard, roles: ["admin", "cashier", "warehouse_worker"] },
  {
    key: "products", icon: Package, roles: ["admin", "cashier"],
    children: [
      { key: "products", href: "/admin/products" },
      { key: "categories", href: "/admin/categories" },
    ],
  },
  {
    key: "orders", icon: ShoppingCart, roles: ["admin", "cashier"],
    children: [
      { key: "orders", href: "/admin/orders" },
      { key: "pos", href: "/admin/pos" },
    ],
  },
  {
    key: "inventory", icon: Warehouse, roles: ["admin", "warehouse_worker"],
    children: [
      { key: "warehouses", href: "/admin/warehouses" },
      { key: "stockMovements", href: "/admin/stock-movements" },
      { key: "purchaseOrders", href: "/admin/purchase-orders" },
      { key: "goodsReceipts", href: "/admin/goods-receipts" },
      { key: "suppliers", href: "/admin/suppliers" },
    ],
  },
  { key: "returns", href: "/admin/returns", icon: RotateCcw, roles: ["admin"] },
  { key: "promoCodes", href: "/admin/promo-codes", icon: Tag, roles: ["admin"] },
  { key: "reports", href: "/admin/reports", icon: BarChart3, roles: ["admin"] },
  { key: "users", href: "/admin/users", icon: Users, roles: ["admin"] },
  { key: "audit", href: "/admin/audit", icon: ScrollText, roles: ["admin"] },
];

const labelCls = (pinned: boolean) =>
  cn(
    "whitespace-nowrap transition-opacity duration-300 ease-in-out",
    // Phones get an icon rail: the label stays for screen readers
    pinned
      ? "max-md:sr-only"
      : "opacity-0 group-hover:opacity-100",
  );

export function AdminSidebar() {
  const pathname = usePathname();
  const locale = useLocale();
  const router = useRouter();
  const tNav = useTranslations("nav");
  const { user } = useCurrentUser();

  const userRole = user?.role;
  const groups = navigationGroups.filter((g) =>
    userRole === "admin" || g.roles.includes(userRole || "")
  );

  const [pinned, setPinned] = useState(true);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    for (const g of groups) {
      if (g.children?.some((c) => pathname.startsWith(c.href))) {
        initial[g.key] = true;
      }
    }
    return initial;
  });

  const handleSignOut = async () => {
    await authClient.signOut();
    router.push("/");
  };

  const toggleGroup = (key: string) => {
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const anyChildActive = (group: NavGroup) =>
    group.children?.some((c) => pathname.startsWith(c.href)) ?? false;

  return (
    <aside
      className={cn(
        "group relative flex min-h-dvh flex-col border-r bg-card transition-all duration-300 ease-in-out",
        pinned ? "w-14 md:w-64" : "w-14 hover:w-64",
      )}
    >
      {/* Pin button */}
      <button
        onClick={() => setPinned(!pinned)}
        className={cn(
          "absolute -right-3 top-6 z-10 flex max-md:hidden h-6 w-6 items-center justify-center rounded-full border bg-background shadow-sm hover:bg-muted transition-opacity duration-300 ease-in-out",
          pinned ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}
        title={pinned ? tNav("unpin") : tNav("pin")}
      >
        {pinned ? <PinOff className="h-3 w-3 text-muted-foreground" /> : <Pin className="h-3 w-3 text-muted-foreground" />}
      </button>

      {/* Logo */}
      <div className="flex items-center gap-3 h-16 shrink-0 px-3 mb-8">
        <Store className="h-6 w-6 shrink-0 text-primary" />
        <span className={cn("text-xl font-bold", labelCls(pinned))}>TechHub</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-1">
        {groups.map((group) => {
          if (!group.children) {
            const isActive = pathname.startsWith(group.href!);
            return (
              <Link
                key={group.key}
                href={group.href!}
                className={cn(
                  "flex items-center gap-3 px-3 justify-start rounded-md py-2 text-sm font-medium transition-colors duration-200",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={tNav(group.key)}
              >
                <group.icon className="h-5 w-5 shrink-0" />
                <span className={labelCls(pinned)}>{tNav(group.key)}</span>
              </Link>
            );
          }

          const isActive = anyChildActive(group);
          const isOpen = openGroups[group.key] ?? false;

          return (
            <Collapsible key={group.key} open={isOpen} onOpenChange={() => toggleGroup(group.key)}>
              <CollapsibleTrigger
                className={cn(
                  "flex items-center gap-3 px-3 justify-start rounded-md py-2 text-sm font-medium transition-colors duration-200 w-full",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title={tNav(group.key)}
              >
                <group.icon className="h-5 w-5 shrink-0" />
                <span className={cn("flex-1 text-left", labelCls(pinned))}>{tNav(group.key)}</span>
                <ChevronDown
                  className={cn(
                    "h-4 w-4 shrink-0 transition-all duration-300 ease-in-out",
                    isOpen && "rotate-180",
                    pinned
                      ? "max-md:hidden"
                      : "opacity-0 group-hover:opacity-100",
                  )}
                />
              </CollapsibleTrigger>
              <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-collapse-up data-[state=open]:animate-collapse-down">
                <div className="mt-1 space-y-1">
                  {group.children.map((child) => {
                    const isChildActive = pathname.startsWith(child.href);
                    return (
                      <Link
                        key={child.key}
                        href={child.href}
                        className={cn(
                          "flex items-center gap-3 pl-6 justify-start rounded-md py-1.5 text-sm font-medium transition-colors duration-200",
                          isChildActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                        title={tNav(child.key)}
                      >
                        <div
                          className={cn(
                            "h-1.5 w-1.5 rounded-full bg-current shrink-0",
                            pinned ? "md:hidden" : "group-hover:hidden",
                          )}
                        />
                        <span className={labelCls(pinned)}>{tNav(child.key)}</span>
                      </Link>
                    );
                  })}
                </div>
              </CollapsibleContent>
            </Collapsible>
          );
        })}
      </nav>

      {/* Bottom links */}
      <div className="mt-auto space-y-1 border-t px-1 pt-4">
        {/* Locale switcher */}
        <div className={cn(
          "flex max-md:flex-col items-center gap-1 px-1 py-1 transition-opacity duration-300 ease-in-out",
          pinned
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100",
        )}>
          {[
            { code: "ua", label: "UA" },
            { code: "en", label: "EN" },
          ].map((lang) => (
            <button
              key={lang.code}
              onClick={() => { window.location.href = `/${lang.code}${pathname}`; }}
              className={cn(
                "min-h-6 px-2 py-1 text-xs rounded font-medium transition-colors",
                lang.code === locale
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {lang.label}
            </button>
          ))}
        </div>

        <Link
          href="/"
          className={cn(
            "flex items-center gap-3 px-2.5 justify-start rounded-md py-2 text-sm font-medium transition-colors duration-200 text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
          title={tNav("toStore")}
        >
          <ExternalLink className="h-5 w-5 shrink-0" />
          <span className={labelCls(pinned)}>{tNav("toStore")}</span>
        </Link>

        <Button
          variant="ghost"
          className={cn(
            "flex items-center gap-3 px-3 justify-start w-full text-muted-foreground hover:text-destructive transition-colors duration-200",
          )}
          onClick={handleSignOut}
          title={tNav("signOut")}
        >
          <LogOut className="h-5 w-5 shrink-0" />
          <span className={labelCls(pinned)}>{tNav("signOut")}</span>
        </Button>
      </div>
    </aside>
  );
}
