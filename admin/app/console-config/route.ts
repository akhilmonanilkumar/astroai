/** Sign-in settings, read at runtime so one image serves every environment. */
export const dynamic = "force-dynamic";

export function GET() {
  const url = process.env.SUPABASE_URL ?? "";
  const key = process.env.SUPABASE_ANON_KEY ?? "";
  return Response.json(url && key ? { mode: "supabase", url, key } : { mode: "dev" });
}
