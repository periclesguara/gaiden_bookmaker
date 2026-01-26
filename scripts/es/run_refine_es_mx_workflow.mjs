import { readFileSync, writeFileSync } from "node:fs";
import { Runner, withTrace } from "@openai/agents";

const WORKFLOW_ID = process.env.OPENAI_WORKFLOW_ID_ES_MX;

const IN_PATH = "data/chunks/book_0001/refine_es_01/merged_es_2025.txt";
const OUT_PATH = "data/chunks/book_0001/refine_es_01/refined_es_mx_2025.txt";

if (!WORKFLOW_ID) throw new Error("Missing OPENAI_WORKFLOW_ID_ES_MX");

const inputText = readFileSync(IN_PATH, "utf-8");

const runner = new Runner({
  traceMetadata: {
    __trace_source__: "gaiden",
    workflow_id: WORKFLOW_ID,
  },
});

const result = await withTrace("Gaiden ES_MX Refine Workflow", async () => {
  return await runner.run(WORKFLOW_ID, [
    { role: "user", content: [{ type: "input_text", text: inputText }] },
  ]);
});

if (!result.finalOutput) throw new Error("Empty output from workflow");

writeFileSync(OUT_PATH, result.finalOutput, "utf-8");
console.log(JSON.stringify({ ok: true, outPath: OUT_PATH }, null, 2));
