import fs from "node:fs";
import path from "node:path";
import OpenAI from "openai";

const ROOT = "/home/periclesguara/Projetos/gaiden_bookmaker";

const INPUT_FILE = path.join(
  ROOT,
  "data/chunks/book_0001/refine_es_01/merged_es_2025.txt"
);

const OUTPUT_FILE = path.join(
  ROOT,
  "data/chunks/book_0001/refine_es_01/refined_es_mx_2025.txt"
);

const REPORT_FILE = path.join(
  ROOT,
  "data/chunks/book_0001/refine_es_01/refined_es_mx_2025.report.txt"
);

const INSTRUCTIONS = `You are a senior literary editor specializing in high-quality neutral Latin American Spanish (Mexico standard).

Your task is to refine, elevate, and linguistically polish a Spanish literary text that was machine-translated from English, producing a final, publication-ready literary Spanish suitable for Mexican and Latin American readers.

This task includes refinement and linguistic polish, but NOT structural normalization.

HARD CONSTRAINTS (must never be violated):
- Do NOT summarize, cut, expand, or reorder content.
- Do NOT add or remove sentences unless a sentence is clearly incomplete or broken due to translation artifacts.
- Do NOT change paragraph structure.
- Do NOT create titles, indexes, footnotes, or frontmatter.
- Do NOT use Markdown or any formatting syntax.
- Do NOT alter proper names, historical terms, places, dates, or chapter numbering.
- Do NOT explain your changes or add comments.

LANGUAGE & STYLE REQUIREMENTS:
- Elevate the Spanish to a polished, modern, literary, publication-grade level.
- Improve fluency, rhythm, cadence, and sentence flow.
- Adjust awkward or broken line breaks that interrupt natural reading.
- Smooth or complete truncated or malformed sentences caused by translation artifacts, without adding new content.
- Reduce light redundancies when they are clearly mechanical or stylistically unnecessary.
- Refine punctuation usage for readability and elegance.
- Replace inappropriate or stiff literal calques from English with natural Spanish constructions.
- Maintain consistent terminology and narrative voice across the entire text.
- Use neutral Latin American Spanish suitable for Mexico.
- Avoid Peninsular Spanish features (no “vosotros”, no “ordenador”, avoid “vale” as filler; use care with “coger”).
- Avoid regional slang; maintain a refined, timeless literary register.
- Avoid archaic or excessively formal constructions that hinder modern readability, while preserving the classical tone of the work.

IMPORTANT LIMITS:
- Do NOT modify structural or editorial elements such as indexes, numbering systems, dialogue markers, or legacy artifacts.
- Structural normalization (indexes, Roman numerals, dialogue punctuation, headings) will be handled separately by deterministic scripts.

OUTPUT REQUIREMENTS:
- Return ONLY the fully refined and linguistically polished text.
- Output must be plain text (TXT).
- Preserve all original paragraphs and line breaks.
`;

function die(msg) {
  console.error(msg);
  process.exit(1);
}

async function main() {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) die("OPENAI_API_KEY not set (load .gaiden_secrets into env before running).");

  if (!fs.existsSync(INPUT_FILE)) die(`Input file not found: ${INPUT_FILE}`);

  const inputText = fs.readFileSync(INPUT_FILE, "utf-8");

  const client = new OpenAI({ apiKey });

  const response = await client.responses.create({
    model: "gpt-5.2-chat-latest",
    input: [
      { role: "system", content: INSTRUCTIONS },
      { role: "user", content: inputText },
    ],
  });

  const out = (response.output_text || "").trimEnd();
  if (!out) die("Empty output_text from model.");

  fs.writeFileSync(OUTPUT_FILE, out + "\n", "utf-8");

  const report = [
    `input_file=${INPUT_FILE}`,
    `output_file=${OUTPUT_FILE}`,
    `input_chars=${inputText.length}`,
    `output_chars=${out.length}`,
    `model=${response.model || "unknown"}`,
    `response_id=${response.id || "unknown"}`,
  ].join("\n");

  fs.writeFileSync(REPORT_FILE, report + "\n", "utf-8");

  console.log("OK: refined ES-MX written:");
  console.log(OUTPUT_FILE);
  console.log("Report:");
  console.log(REPORT_FILE);
}

main().catch((err) => {
  console.error("ERROR:", err?.stack || err);
  process.exit(1);
});
