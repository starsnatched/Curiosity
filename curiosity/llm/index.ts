import OpenAI from "openai";

const openai = new OpenAI({
  baseURL: "http://localhost:11434/v1/",
  apiKey: "ollama", // required but ignored
});

const responsesResult = await openai.responses.create({
  model: "qwen",
  input: "say this is a test"
});

console.log(responsesResult.output_text);