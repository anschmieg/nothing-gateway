package handlers

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/interfaces"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/registry"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/thinking"
	coreexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/translator"
)

type responsesPluginRouteKey struct{}

func (h *BaseAPIHandler) admitResponsesPluginRoute(ctx context.Context, entry, exit, model string, raw []byte, alt string, opts modelExecutionOptions) (context.Context, bool, *interfaces.ErrorMessage) {
	base := thinking.ParseSuffix(model).ModelName
	if h.AuthManager == nil || !h.AuthManager.CanonicalRoutingEnabled() || !strings.HasPrefix(base, registry.ResponsesPluginID+"/") {
		return ctx, false, nil
	}
	fail := func(status int, message string) (context.Context, bool, *interfaces.ErrorMessage) {
		return ctx, true, &interfaces.ErrorMessage{StatusCode: status, Error: errors.New(message)}
	}
	if h.AuthManager.HomeEnabled() {
		return fail(http.StatusServiceUnavailable, "plugin executor routing is unavailable while Home is enabled")
	}
	path := opts.Path
	if path == "" {
		path, _ = requestExecutionMetadata(ctx)[coreexecutor.RequestPathMetadataKey].(string)
	}
	response := string(translator.FormatOpenAIResponse)
	request, _ := ctx.Value("gin").(*gin.Context)
	if request == nil || request.Request == nil || request.Request.Method != http.MethodPost ||
		entry != response || exit != response || opts.InternalSource || registry.ModelProfileFromContext(ctx) != "" ||
		!((path == "/v1/responses" && alt == "") || (path == "/v1/responses/compact" && alt == "responses/compact")) {
		return fail(http.StatusBadRequest, "use the explicit OpenAI Responses plugin route on /v1/responses or /v1/responses/compact")
	}
	if _, err := canonicalRequestRequirements(raw); err != nil {
		return fail(http.StatusBadRequest, "invalid or ambiguous JSON request")
	}
	policy := registry.RequestBillingPolicy(ctx, "", strings.HasSuffix(base, ":free"))
	if trace := registry.RoutingTraceFromContext(ctx); trace != nil {
		trace.Configure("", model, policy, strings.HasSuffix(base, ":free"))
	}
	// The project key may incur charges. A static registration is not evidence
	// of a free tier or remaining credits.
	if !policy.Policy.Allows(registry.BillingUnknown) {
		return fail(http.StatusForbidden, "the OpenAI Responses plugin requires explicit PAID session policy")
	}
	if model != base || !registry.GetGlobalRegistry().ResponsesPluginRouteAvailable(model) {
		return fail(http.StatusServiceUnavailable, "the exact OpenAI Responses plugin model is unavailable")
	}
	return context.WithValue(ctx, responsesPluginRouteKey{}, model), true, nil
}

// Check the exact registered target again after request interception. This is a
// single direct executor call with no auth retries or fallback to native routes.
func validateResponsesPluginDispatch(ctx context.Context, pluginID, model string) *interfaces.ErrorMessage {
	bound, _ := ctx.Value(responsesPluginRouteKey{}).(string)
	if bound == "" {
		return nil
	}
	if pluginID != registry.ResponsesPluginID || model != bound || !registry.GetGlobalRegistry().ResponsesPluginRouteAvailable(bound) {
		return &interfaces.ErrorMessage{StatusCode: http.StatusConflict, Error: errors.New("OpenAI Responses plugin route changed before dispatch")}
	}
	if trace := registry.RoutingTraceFromContext(ctx); trace != nil {
		trace.Select(registry.BillingUnknown, model, registry.ResponsesPluginID)
	}
	return nil
}
