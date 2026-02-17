import fs from "node:fs";
import OpenAI from "openai";

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { in: null, out: null };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];

    if (a === "--in" && args[i + 1]) { out.in = args[++i]; continue; }
    if (a === "--out" && args[i + 1]) { out.out = args[++i]; continue; }

    if (!a.startsWith("--")) {
      if (!out.in) { out.in = a; continue; }
      if (!out.out) { out.out = a; continue; }
    }
  }

  return out;
}

const { in: inPathArg, out: outPathArg } = parseArgs();

if (!inPathArg || !outPathArg) {
  console.error("Usage: refine_one_part.mjs --in INPUT --out OUTPUT  OR  refine_one_part.mjs INPUT OUTPUT");
  process.exit(2);
}

const inputPath = inPathArg;
const outputPath = outPathArg;

const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) {
  console.error("Missing OPENAI_API_KEY (load .gaiden_secrets into env)");
  process.exit(1);
}

const INSTRUCTIONS = `You are a senior literary editor specializing in high-quality neutral Latin American Spanish (Mexico standard).

TASK:
Refine and linguistically polish the provided Spanish literary text segment (machine-translated), producing publication-ready Spanish for Mexico/Latin America.

HARD CONSTRAINTS:
- Do NOT summarize, cut, expand, or reorder content.
- Do NOT add meta text, editorial notes, disclaimers, or commentary.
  (Examples forbidden: "A partir de este punto...", "El relato continúa...", "Como IA...", "Nota del editor...")
- Do NOT add any extra lines not present in the input, except strictly necessary linguistic fixes inside the text.
- Do NOT change paragraph boundaries.
- Do NOT create titles, indexes, footnotes, or frontmatter.
- Do NOT use Markdown.
- Do NOT alter proper names, places, dates, or numbering.

STYLE:
- Elevate fluency, rhythm, cadence.
- Fix awkward calques, punctuation, truncated sentences caused by translation artifacts.
- Neutral LatAm Spanish (Mexico), no "vosotros", avoid Peninsular fillers, avoid slang.

OUTPUT REQUIREMENTS:
- Return ONLY the refined text segment.
- Output must end with the last sentence of the provided input segment.
- Preserve original paragraphs and line breaks.
`;

async function main() {
  const inputText = fs.readFileSync(inputPath, "utf-8");
  const client = new OpenAI({ apiKey });

  const resp = await client.responses.create({
    model: "gpt-5.2-chat-latest",
    max_output_tokens: 12000,
    input: [
      { role: "system", content: INSTRUCTIONS },
      { role: "user", content: inputText }
    ],
  });

  const out = (resp.output_text || "").trimEnd();
  if (!out) throw new Error("Empty output_text");
  fs.writeFileSync(outputPath, out + "\n", "utf-8");
  console.log(`OK ${inputPath} -> ${outputPath}`);
}

main().catch(e => {
  const err = e && e.stack ? e.stack : e;
  console.error("ERROR:", err);
  process.exit(1);
});
