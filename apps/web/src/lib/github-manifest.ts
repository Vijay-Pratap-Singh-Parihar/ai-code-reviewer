/**
 * GitHub's manifest flow requires a real top-level form POST (not fetch):
 * the user's browser carries the manifest to github.com, where they confirm
 * the new App while signed in to GitHub.
 */
export function submitManifestForm(actionUrl: string, manifest: Record<string, unknown>): void {
  const form = document.createElement("form");
  form.method = "post";
  form.action = actionUrl;
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "manifest";
  input.value = JSON.stringify(manifest);
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
}
