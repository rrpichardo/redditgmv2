// Small, safe Markdown renderer for generated report previews.
// Raw HTML is escaped before any formatting is applied.
import { esc } from "./state.js";

function inlineMarkdown(value) {
  return esc(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/_([^_]+)_/g, "<em>$1</em>");
}

export function renderSafeMarkdown(markdown = "") {
  const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
  const output = [];
  let listType = "";

  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = "";
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    const unordered = /^[-*]\s+(.+)$/.exec(line);
    const ordered = /^\d+\.\s+(.+)$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length + 2;
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
    } else if (unordered || ordered) {
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        output.push(`<${listType}>`);
      }
      output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
    } else if (line.startsWith("> ")) {
      closeList();
      output.push(`<blockquote>${inlineMarkdown(line.slice(2))}</blockquote>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      output.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  }
  closeList();
  return output.join("");
}
