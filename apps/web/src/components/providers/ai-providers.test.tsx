import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AiProviders } from "@/components/providers/ai-providers";
import {
  createProvider,
  deleteProvider,
  listModelRoutes,
  listProviderKinds,
  listProviderModels,
  listProviders,
  setModelRoutes,
  testProviderLive,
  updateProvider,
  type ProviderKindInfo,
  type ProviderPublic,
} from "@/lib/api-client";
import { setAuthState } from "@/lib/auth-store";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    listProviderKinds: vi.fn(),
    listProviders: vi.fn(),
    listModelRoutes: vi.fn(),
    createProvider: vi.fn(),
    updateProvider: vi.fn(),
    deleteProvider: vi.fn(),
    testProviderLive: vi.fn(),
    listProviderModels: vi.fn(),
    setModelRoutes: vi.fn(),
  };
});

const KINDS: ProviderKindInfo[] = [
  { kind: "anthropic", label: "Anthropic", needs_api_key: true, needs_base_url: false, default_base_url: null, available: true },
  { kind: "groq", label: "Groq", needs_api_key: true, needs_base_url: false, default_base_url: null, available: true },
  {
    kind: "openai_compatible",
    label: "OpenAI-compatible endpoint",
    needs_api_key: false,
    needs_base_url: true,
    default_base_url: null,
    available: true,
    presets: [
      { label: "Ollama on this machine", url: "http://127.0.0.1:11434/v1" },
      { label: "LM Studio on this machine", url: "http://127.0.0.1:1234/v1" },
    ],
  },
  { kind: "bedrock", label: "AWS Bedrock", needs_api_key: true, needs_base_url: false, default_base_url: null, available: false },
];

function provider(overrides: Partial<ProviderPublic> = {}): ProviderPublic {
  return {
    id: "p1",
    name: "Groq",
    kind: "groq",
    kind_label: "Groq",
    base_url: null,
    effective_base_url: "https://api.groq.com/openai/v1",
    key_hint: "gsk…1234",
    has_api_key: true,
    header_names: [],
    settings: {},
    verified_at: null,
    last_test_error: null,
    created_at: "2026-10-08T00:00:00Z",
    updated_at: null,
    used_by: [],
    ...overrides,
  };
}

function signInAs(role: "owner" | "member") {
  setAuthState({
    accessToken: "token",
    status: "authenticated",
    user: { id: "u1", org_id: "o1", email: "me@example.com", role },
  });
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AiProviders />
    </QueryClientProvider>,
  );
}

describe("AiProviders", () => {
  beforeEach(() => {
    vi.mocked(listProviderKinds).mockReset().mockResolvedValue(KINDS);
    vi.mocked(listProviders).mockReset().mockResolvedValue([]);
    vi.mocked(listModelRoutes).mockReset().mockResolvedValue([]);
    vi.mocked(createProvider).mockReset().mockResolvedValue(provider());
    vi.mocked(updateProvider).mockReset().mockResolvedValue(provider());
    vi.mocked(deleteProvider).mockReset().mockResolvedValue();
    vi.mocked(setModelRoutes).mockReset().mockResolvedValue([]);
    vi.mocked(testProviderLive).mockReset();
    vi.mocked(listProviderModels).mockReset().mockResolvedValue({ models: ["qwen3.5:4b", "qwen3.5:9b"] });
    signInAs("owner");
  });
  afterEach(() => setAuthState({ accessToken: null, user: null, status: "unauthenticated" }));

  it("warns that reviews are refused until a review model is chosen", async () => {
    renderPage();

    expect(await screen.findByText(/no review model yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no providers yet/i)).toBeInTheDocument();
  });

  it("adds a local OpenAI-compatible model with a preset URL and no key", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /add provider/i }));
    await user.click(screen.getByRole("combobox", { name: /provider/i }));
    await user.click(await screen.findByRole("option", { name: /openai-compatible endpoint/i }));
    expect(screen.getByRole("option", { hidden: true, name: /aws bedrock \(coming soon\)/i })).toBeInTheDocument();
    // A name is suggested, and password managers are told to leave it alone
    // (they used to fill it with the user's email).
    const name = screen.getByLabelText("Display name");
    expect(name).toHaveValue("My model server");
    expect(name).toHaveAttribute("autocomplete", "off");
    expect(screen.getByLabelText(/api key/i)).toHaveAttribute("autocomplete", "new-password");
    // Presets come from the API, with the host revu can reach.
    await user.click(screen.getByRole("button", { name: /ollama on this machine/i }));
    expect(name).toHaveValue("Local Ollama");
    expect(screen.getByLabelText("Endpoint URL")).toHaveValue("http://127.0.0.1:11434/v1");
    await user.click(screen.getByRole("button", { name: /^add provider$/i }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith({
        name: "Local Ollama",
        kind: "openai_compatible",
        base_url: "http://127.0.0.1:11434/v1",
        api_key: null,
        settings: { input_cost_per_mtok: null, output_cost_per_mtok: null },
      }),
    );
  });

  it("shows a provider by its key hint and capabilities, never its key", async () => {
    vi.mocked(listProviders).mockResolvedValue([
      provider({
        settings: { supports_json: true, supports_tools: false },
        used_by: ["review"],
        verified_at: "2026-10-08T00:00:00Z",
      }),
    ]);

    renderPage();

    expect(await screen.findByText(/key gsk…1234/)).toBeInTheDocument();
    expect(screen.getByText("JSON")).toBeInTheDocument();
    expect(screen.getByText("no tool calling")).toBeInTheDocument();
    expect(screen.getByText("review")).toBeInTheDocument();
    expect(screen.getByText(/last test passed/i)).toBeInTheDocument();
  });

  it("keeps the stored key when editing without typing a new one, and replaces it when one is typed", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider()]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /edit/i }));
    expect(screen.getByLabelText(/api key/i)).toHaveAttribute("placeholder", "Leave empty to keep gsk…1234");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(updateProvider).toHaveBeenCalledTimes(1));
    expect(vi.mocked(updateProvider).mock.calls[0][1]).not.toHaveProperty("api_key");

    await user.click(await screen.findByRole("button", { name: /edit/i }));
    await user.type(screen.getByLabelText(/api key/i), "gsk_new_key");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(updateProvider).toHaveBeenCalledTimes(2));
    expect(vi.mocked(updateProvider).mock.calls[1][1]).toMatchObject({ api_key: "gsk_new_key" });
  });

  it("shows each call of a connection test as it runs, then offers the model for reviews", async () => {
    vi.mocked(listProviders).mockResolvedValue([
      provider({ id: "p2", name: "Local Ollama", kind: "openai_compatible", kind_label: "OpenAI-compatible" }),
    ]);
    const result = {
      ok: true,
      model: "qwen3.5:4b",
      reply: { ok: true, detail: "OK", latency_ms: 1200 },
      json_mode: { ok: true, detail: null, latency_ms: 900 },
      tool_calling: { ok: false, detail: "the model answered without calling the tool", latency_ms: 800 },
      cost_usd: 0,
    };
    let finish: () => void = () => {};
    vi.mocked(testProviderLive).mockImplementation(
      (_id, _body, onEvent) =>
        new Promise((resolve) => {
          onEvent({ event: "start", step: "reply" });
          onEvent({ event: "step", step: "reply", result: result.reply });
          onEvent({ event: "start", step: "json_mode" });
          finish = () => {
            onEvent({ event: "step", step: "json_mode", result: result.json_mode });
            onEvent({ event: "start", step: "tool_calling" });
            onEvent({ event: "step", step: "tool_calling", result: result.tool_calling });
            onEvent({ event: "done", result });
            resolve(result);
          };
        }),
    );
    vi.mocked(setModelRoutes).mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^test$/i }));
    const dialog = await screen.findByRole("dialog");
    // The model list loads by itself; pick from it.
    await user.click(within(dialog).getByLabelText("Model"));
    await user.click(await screen.findByRole("option", { name: "qwen3.5:4b" }));
    // A local model is tested with thinking off unless chosen otherwise.
    expect(within(dialog).getByRole("combobox", { name: "Thinking" })).toHaveTextContent("Off (fastest)");
    await user.click(within(dialog).getByRole("button", { name: /run test/i }));

    // Mid-test: the first call passed, the second is running, the third waits.
    expect(await within(dialog).findByLabelText("running")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("passed")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("waiting")).toBeInTheDocument();
    expect(within(dialog).getByText("Answered “OK”")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /stop/i })).toBeInTheDocument();
    expect(testProviderLive).toHaveBeenCalledWith(
      "p2",
      { model: "qwen3.5:4b", reasoning: "off" },
      expect.any(Function),
      expect.anything(),
    );

    finish();
    expect(await within(dialog).findByText(/ready for reviews/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/cross-file reviews run as diff-only/i)).toBeInTheDocument();
    expect(within(dialog).getByText("the model answered without calling the tool")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /use for reviews/i }));
    await waitFor(() =>
      expect(setModelRoutes).toHaveBeenCalledWith({
        review: { provider_id: "p2", model: "qwen3.5:4b", reasoning: "off" },
      }),
    );
    expect(await within(dialog).findByText(/reviews now use qwen3.5:4b/i)).toBeInTheDocument();
  });

  it("saves the review model and leaves unset steps empty", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider()]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("combobox", { name: /review provider/i }));
    await user.click(await screen.findByRole("option", { name: "Groq" }));
    // Any id can be typed, listed or not.
    await user.type(screen.getByLabelText(/review model/i), "openai/gpt-oss-120b");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: /save models/i }));

    await waitFor(() =>
      expect(setModelRoutes).toHaveBeenCalledWith({
        review: { provider_id: "p1", model: "openai/gpt-oss-120b", reasoning: null },
        screen: null,
        verify: null,
      }),
    );
  });

  it("saves a thinking level per step and shows the saved one", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider()]);
    vi.mocked(listModelRoutes).mockResolvedValue([
      {
        tier: "review",
        provider_id: "p1",
        provider_name: "Groq",
        model: "qwen3.5:4b",
        reasoning: "high",
        updated_at: null,
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    const thinking = await screen.findByRole("combobox", { name: /review thinking/i });
    expect(thinking).toHaveTextContent("Thinking: High");
    expect(screen.getByRole("combobox", { name: /screen thinking/i })).toBeDisabled();

    await user.click(thinking);
    await user.click(await screen.findByRole("option", { name: "Off (fastest)" }));
    await user.click(screen.getByRole("button", { name: /save models/i }));

    await waitFor(() =>
      expect(setModelRoutes).toHaveBeenCalledWith({
        review: { provider_id: "p1", model: "qwen3.5:4b", reasoning: "off" },
        screen: null,
        verify: null,
      }),
    );
    // Saving re-mounts the card with the saved routes; the confirmation survives that.
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("deletes a provider after its name is typed", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider({ used_by: ["review"] })]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^delete$/i }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/reviews are refused while the review step has none/i)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/to confirm/i), "Groq");
    await user.click(within(dialog).getByRole("button", { name: /delete permanently/i }));

    await waitFor(() => expect(deleteProvider).toHaveBeenCalledWith("p1"));
  });

  it("is read-only for members", async () => {
    signInAs("member");
    vi.mocked(listProviders).mockResolvedValue([provider()]);

    renderPage();

    expect(await screen.findByText(/key gsk…1234/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add provider/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit|^test$|^delete$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save models/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only owners and admins can change models/i)).toBeInTheDocument();
  });
});
