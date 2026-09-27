import { site } from "@/site";

export function LegalPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <article className="legal">
      <h1>{title}</h1>
      <div className="meta">Last updated {site.lastUpdated}</div>
      <p className="draft">
        Draft for legal review. Company details are pending incorporation.
      </p>
      {children}
    </article>
  );
}
