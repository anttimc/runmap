/** Resolve required UI elements and report missing markup during setup. */
/** Fail at setup when the static markup and a controller disagree. */
export function element<T extends HTMLElement = HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing #${id}`);
  return found as T;
}
