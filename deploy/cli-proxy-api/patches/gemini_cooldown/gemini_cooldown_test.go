package executor

import (
	"context"
	"errors"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/config"
	cliproxyauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
	sdktranslator "github.com/router-for-me/CLIProxyAPI/v8/sdk/translator"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestGeminiCooldownMetadata(t *testing.T) {
	depleted := []byte(`{"error":{"code":402,"message":"Your prepayment credits are depleted.","status":"RESOURCE_EXHAUSTED"}}`)
	err := newGeminiStatusError(402, nil, depleted)
	if err.StatusCode() != 402 || err.Error() != string(depleted) || !err.IsCredentialScoped() || err.RetryAfter() == nil || *err.RetryAfter() != 30*time.Minute {
		t.Fatalf("credit metadata lost: %+v", err)
	}
	for _, status := range []int{402, 429, 503} {
		ordinary := newGeminiStatusError(status, nil, []byte(`{"error":{"status":"RESOURCE_EXHAUSTED","message":"Rate limit for this model"}}`))
		if ordinary.IsCredentialScoped() {
			t.Fatalf("model limit incorrectly affects credential: %d", status)
		}
	}
	hint := newGeminiStatusError(503, http.Header{"Retry-After": []string{"45"}}, []byte(`{"error":{"status":"UNAVAILABLE"}}`))
	if hint.RetryAfter() == nil || *hint.RetryAfter() != 45*time.Second {
		t.Fatalf("header lost: %+v", hint)
	}
	hint = newGeminiStatusError(429, nil, []byte(`{"error":{"details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"60s"}]}}`))
	if hint.RetryAfter() == nil || *hint.RetryAfter() != time.Minute {
		t.Fatalf("body retry lost: %+v", hint)
	}
}

func TestGeminiCreditErrorPropagatesStreamAndNonStream(t *testing.T) {
	body := `{"error":{"code":402,"message":"Your prepayment credits are depleted.","status":"RESOURCE_EXHAUSTED"}}`
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Retry-After", "120")
		w.WriteHeader(402)
		_, _ = w.Write([]byte(body))
	}))
	defer server.Close()
	executor := NewGeminiExecutor(&config.Config{})
	auth := &cliproxyauth.Auth{Provider: "gemini", Attributes: map[string]string{"api_key": "fixture", "base_url": server.URL}}
	req := cliproxyexecutor.Request{Model: "gemini-3.8-flash", Payload: []byte(`{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`)}
	for _, stream := range []bool{false, true} {
		opts := cliproxyexecutor.Options{SourceFormat: sdktranslator.FormatGemini, Stream: stream}
		var err error
		if stream {
			_, err = executor.ExecuteStream(context.Background(), auth, req, opts)
		} else {
			_, err = executor.Execute(context.Background(), auth, req, opts)
		}
		var status statusErr
		if !errors.As(err, &status) || status.StatusCode() != 402 || status.Error() != body || !status.IsCredentialScoped() || status.RetryAfter() == nil || *status.RetryAfter() != 2*time.Minute {
			t.Fatalf("stream=%t error metadata lost: %+v", stream, err)
		}
	}
}
