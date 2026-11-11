// Print layout for every worksheet of an exported workbook.
// Excel only honours fitToWidth/fitToHeight when <sheetPr><pageSetUpPr fitToPage="1"/> is present; without it each
// sheet printed at 100% and wide tables were cut into many pages. Each sheet gets A4, fit to one page wide, the
// orientation that needs fewer pages, and one page tall when it nearly fits (≤15% extra shrink).
const PAGE = {
  portrait: { w: 595.3 - 2 * 18, h: 841.9 - 2 * 22 - 16 },
  landscape: { w: 841.9 - 2 * 18, h: 595.3 - 2 * 22 - 16 },
};
const colIndex = (letters) => [...letters].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0);
const attr = (text, name) => text.match(new RegExp("\\b" + name + '="([^"]*)"'))?.[1];

export function sheetExtent(xml) {
  const fmt = xml.match(/<sheetFormatPr\b([^>]*)\/?>/)?.[1] ?? "";
  const defaultWidth = Number(attr(fmt, "defaultColWidth") ?? (Number(attr(fmt, "baseColWidth") ?? 8) + 0.43));
  const defaultHeight = Number(attr(fmt, "defaultRowHeight") ?? 15);
  let maxCol = 0,
    maxRow = 0;
  for (const m of xml.matchAll(/<c\b[^>]*\br="([A-Z]+)(\d+)"/g)) {
    maxCol = Math.max(maxCol, colIndex(m[1]));
    maxRow = Math.max(maxRow, Number(m[2]));
  }
  for (const m of xml.matchAll(/<mergeCell\b[^>]*ref="[A-Z]+\d+:([A-Z]+)(\d+)"/g)) {
    maxCol = Math.max(maxCol, colIndex(m[1]));
    maxRow = Math.max(maxRow, Number(m[2]));
  }
  const widths = new Array(maxCol + 1).fill(defaultWidth);
  for (const m of xml.matchAll(/<col\b([^>]*)\/?>/g)) {
    const min = Number(attr(m[1], "min")),
      max = Number(attr(m[1], "max")),
      width = Number(attr(m[1], "width") ?? defaultWidth),
      hidden = attr(m[1], "hidden") === "1";
    for (let c = min; c <= Math.min(max, maxCol); c++) widths[c] = hidden ? 0 : width;
  }
  const heights = new Map();
  for (const m of xml.matchAll(/<row\b([^>]*)>/g)) {
    const r = Number(attr(m[1], "r"));
    if (!r) continue;
    heights.set(r, attr(m[1], "hidden") === "1" ? 0 : Number(attr(m[1], "ht") ?? defaultHeight));
  }
  let height = 0;
  for (let r = 1; r <= maxRow; r++) height += heights.get(r) ?? defaultHeight;
  // Excel column width unit ≈ one "0" of the default font ≈ 7 px ≈ 5.25 pt, plus cell padding.
  const width = widths.slice(1).reduce((a, w) => a + (w > 0 ? w * 5.25 + 3.75 : 0), 0);
  return { width, height, maxCol, maxRow };
}

export function choosePrintLayout({ width, height }) {
  const options = Object.entries(PAGE).map(([orientation, page]) => {
    const fitWidth = Math.min(1, page.w / Math.max(width, 1));
    const rawPages = (height * fitWidth) / page.h;
    const pages = Math.max(1, Math.ceil(rawPages - 0.15));
    const scale = pages === 1 ? Math.min(fitWidth, page.h / Math.max(height, 1)) : fitWidth;
    return { orientation, pages, scale };
  });
  options.sort((a, b) => a.pages - b.pages || b.scale - a.scale);
  const best = options[0];
  return { orientation: best.orientation, fitToHeight: best.pages === 1 ? 1 : 0, pages: best.pages, scale: best.scale };
}

const LATER = /<(headerFooter|rowBreaks|colBreaks|customProperties|cellWatches|ignoredErrors|smartTags|drawing|legacyDrawing|legacyDrawingHF|picture|oleObjects|controls|webPublishItems|tableParts|extLst)\b/;

export function printReadySheet(xml) {
  // OOXML formulas never start with "="; some source workbooks stored them that way.
  xml = xml.replace(/(<f(?:\s[^>]*)?>)=+/g, "$1");
  const layout = choosePrintLayout(sheetExtent(xml));
  if (/<sheetPr\b[^>]*\/>/.test(xml)) xml = xml.replace(/<sheetPr\b([^>]*)\/>/, '<sheetPr$1><pageSetUpPr fitToPage="1"/></sheetPr>');
  else if (/<sheetPr\b/.test(xml)) {
    if (/<pageSetUpPr\b/.test(xml))
      xml = xml.replace(/<pageSetUpPr\b([^>]*?)(?:\/>|>[\s\S]*?<\/pageSetUpPr>)/, (m, a) => '<pageSetUpPr' + a.replace(/\s*fitToPage="[^"]*"/, "") + ' fitToPage="1"/>');
    else xml = xml.replace("</sheetPr>", '<pageSetUpPr fitToPage="1"/></sheetPr>');
  } else xml = xml.replace(/(<worksheet\b[^>]*>)/, '$1<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>');
  xml = xml
    .replace(/<printOptions\b[^>]*\/>/g, "")
    .replace(/<pageMargins\b[^>]*\/>/g, "")
    .replace(/<pageSetup\b[^>]*\/>/g, "")
    .replace(/<pageSetup\b[^>]*>[\s\S]*?<\/pageSetup>/g, "");
  const block =
    '<printOptions horizontalCentered="1"/><pageMargins left="0.25" right="0.25" top="0.3" bottom="0.3" header="0.15" footer="0.15"/>' +
    '<pageSetup paperSize="9" orientation="' +
    layout.orientation +
    '" fitToWidth="1" fitToHeight="' +
    layout.fitToHeight +
    '"/>';
  const later = xml.match(LATER);
  xml = later ? xml.slice(0, later.index) + block + xml.slice(later.index) : xml.replace("</worksheet>", block + "</worksheet>");
  return { xml, layout };
}

/** Apply print layout to every worksheet part of a JSZip workbook. Returns {sheetPath: layout}. */
export async function printReadyWorkbook(zip) {
  const report = {};
  for (const path of Object.keys(zip.files).filter((p) => /^xl\/worksheets\/[^/]+\.xml$/.test(p))) {
    const { xml, layout } = printReadySheet(await zip.file(path).async("string"));
    zip.file(path, xml);
    report[path] = layout;
  }
  return report;
}

/** Blob → print-ready Blob. */
export async function printReadyXlsx(blob) {
  if (!globalThis.JSZip) return blob;
  const zip = await globalThis.JSZip.loadAsync(await blob.arrayBuffer());
  await printReadyWorkbook(zip);
  return new Blob([await zip.generateAsync({ type: "uint8array", compression: "DEFLATE" })], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}
