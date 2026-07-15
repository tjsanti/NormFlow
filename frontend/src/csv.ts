function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result ?? "")));
    reader.addEventListener("error", () => reject(new Error("Could not read the selected CSV.")));
    reader.readAsText(file);
  });
}

function firstCsvRecord(contents: string): string[] {
  const headers: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < contents.length; index += 1) {
    const character = contents[index];
    if (quoted) {
      if (character === '"' && contents[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
    } else if (character === '"' && field === "") {
      quoted = true;
    } else if (character === ",") {
      headers.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      headers.push(field);
      return headers;
    } else {
      field += character;
    }
  }

  if (quoted) throw new Error("The CSV header row has an unterminated quoted field.");
  headers.push(field);
  return headers;
}

export async function readCsvHeaders(file: File): Promise<string[]> {
  const contents = await readFileText(file);
  if (!contents) throw new Error("The CSV is empty and has no header row.");
  const headers = firstCsvRecord(contents);
  headers[0] = headers[0].replace(/^\uFEFF/, "");
  if (!headers.some((header) => header !== "")) {
    throw new Error("The CSV is empty and has no header row.");
  }
  return headers;
}
