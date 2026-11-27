/**
 * The buyer's wording is the RFQ requirement. Picking a Material Master suggestion only
 * fills the description when the typed text is a search fragment of that material's name;
 * a fuller requirement ("RGB Flood Light") is never replaced by the catalogue name.
 */
export function keepBuyerDescription(typed: string, materialName: string): string {
  const norm = (value: string) => value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const words = norm(typed).split(' ').filter(Boolean);
  const name = ` ${norm(materialName)} `;
  return !words.length || words.every(word => name.includes(` ${word}`)) ? materialName : typed.trim();
}
