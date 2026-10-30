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
  listProviders,
  setModelRoutes,
  testProvider,
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
    testProvider: vi.fn(),
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
    vi.mocked(testProvider).mockReset();
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
    await user.type(screen.getByLabelText("Name"), "Local Ollama");
    await user.click(screen.getByRole("button", { name: /ollama on this machine/i }));
    await user.click(screen.getByRole("button", { name: /^add provider$/i }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith({
        name: "Local Ollama",
        kind: "openai_compatible",
        base_url: "http://host.docker.internal:11434/v1",
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

  it("runs a connection test and explains a model without tool calling", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider()]);
    vi.mocked(testProvider).mockResolvedValue({
      ok: true,
      model: "qwen3.5:9b",
      reply: { ok: true, detail: "OK", latency_ms: 1200 },
      json_mode: { ok: true, detail: null, latency_ms: 900 },
      tool_calling: { ok: false, detail: "the model answered without calling the tool", latency_ms: 800 },
      cost_usd: 0,
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^test$/i }));
    await user.type(screen.getByLabelText("Model"), "qwen3.5:9b");
    await user.click(screen.getByRole("button", { name: /run test/i }));

    expect(await screen.findByText(/ready for reviews/i)).toBeInTheDocument();
    expect(testProvider).toHaveBeenCalledWith("p1", "qwen3.5:9b");
    expect(screen.getByText(/run as diff-only reviews instead/i)).toBeInTheDocument();
  });

  it("saves the review model and leaves unset steps empty", async () => {
    vi.mocked(listProviders).mockResolvedValue([provider()]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("combobox", { name: /review provider/i }));
    await user.click(await screen.findByRole("option", { name: "Groq" }));
    await user.type(screen.getByLabelText(/review model/i), "openai/gpt-oss-120b");
    await user.click(screen.getByRole("button", { name: /save models/i }));

    await waitFor(() =>
      expect(setModelRoutes).toHaveBeenCalledWith({
        review: { provider_id: "p1", model: "openai/gpt-oss-120b" },
        screen: null,
        verify: null,
      }),
    );
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
