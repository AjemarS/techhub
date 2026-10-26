import { getTranslations } from "next-intl/server";
import { getLocale } from "next-intl/server";

import { Link } from "~/i18n/navigation";
import type { Category } from "~/lib/types";
import { catalogFetch } from "~/lib/api/catalog-server";

import { cn } from "~/lib/cn";

/** The real catalog's first categories; an unreachable catalog just leaves "All products" */
async function getFooterCategories(locale: string): Promise<Category[]> {
  try {
    const res = await catalogFetch("/categories/", {
      headers: { "Accept-Language": locale === "en" ? "en" : "uk" },
      next: { revalidate: 300 },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.results ?? data).filter((c: Category) => !c.parent).slice(0, 4);
  } catch {
    return [];
  }
}

export async function Footer({ className }: { className?: string }) {
  const t = await getTranslations("footer");
  const categories = await getFooterCategories(await getLocale());
  const currentYear = new Date().getFullYear();

  return (
    <footer className={cn("border-t bg-background", className)}>
        {/* Footer links section */}
      <div className="container mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-4">
          <div className="space-y-4">
            <Link className="flex items-center gap-2" href="/">
              <span className="bg-linear-to-r from-primary to-primary/70 bg-clip-text text-xl font-bold tracking-tight text-transparent">
                TechHub
              </span>
            </Link>
            <p className="text-sm text-muted-foreground">
              {t("tagline")}
            </p>
          </div>
          <div>
            <h3 className="mb-4 text-sm font-semibold">{t("categories")}</h3>
            <ul className="space-y-2 text-sm">
              <li><Link className="text-muted-foreground hover:text-foreground" href="/products">{t("allProducts")}</Link></li>
              {categories.map((c) => (
                <li key={c.id}>
                  <Link className="text-muted-foreground hover:text-foreground" href={`/products?category=${c.id}`}>{c.name}</Link>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-4 text-sm font-semibold">{t("information")}</h3>
            <ul className="space-y-2 text-sm">
              <li><Link className="text-muted-foreground hover:text-foreground" href="/about">{t("about")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/delivery-payment">{t("deliveryPayment")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/warranty">{t("warranty")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/contacts">{t("contacts")}</Link></li>
            </ul>
          </div>
          <div>
            <h3 className="mb-4 text-sm font-semibold">{t("support")}</h3>
            <ul className="space-y-2 text-sm">
              <li><Link className="text-muted-foreground hover:text-foreground" href="/faq">{t("help")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/my/orders">{t("myOrders")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/privacy">{t("privacy")}</Link></li>
              <li><Link className="text-muted-foreground hover:text-foreground" href="/terms">{t("terms")}</Link></li>
            </ul>
          </div>
        </div>
        <div className="mt-12 border-t pt-8">
          <div className="flex flex-col items-center justify-between gap-4 md:flex-row">
            <p className="text-sm text-muted-foreground">
              {t("copyright", { year: currentYear })}
            </p>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <Link className="text-muted-foreground hover:text-foreground" href="/privacy">{t("privacyShort")}</Link>
              <Link className="text-muted-foreground hover:text-foreground" href="/terms">{t("termsShort")}</Link>
            </div>
          </div>
        </div>
      </div>
    </footer>
  );
}
