(function initOidcModule() {
    const config = window.OIDC_CONFIG || {};
    const OIDC_ISSUER_URL = String(config.issuerUrl || "").trim();
    const OIDC_CLIENT_ID = String(config.clientId || "").trim();
    const OIDC_BASE_URI = String(config.baseUri || "").trim();

    const ACCESS_TOKEN_KEY = "access_token";
    const REFRESH_TOKEN_KEY = "refresh_token";
    const EXPIRES_AT_KEY = "token_expiry";
    const PKCE_VERIFIER_KEY = "pkce_verifier";
    const OIDC_STATE_KEY = "oidc_state";
    const OIDC_NONCE_KEY = "oidc_nonce";
    const FATAL_OIDC_ERRORS = new Set([
        "invalid_oidc_configuration",
        "invalid_scope",
        "invalid_client",
        "unauthorized_client",
        "invalid_request",
        "invalid_redirect_uri",
        "browser_crypto_unavailable",
    ]);

    const TOKEN_REFRESH_SAFETY_SECONDS = 60;
    let oidcMetadataPromise = null;
    let refreshPromise = null;
    let fatalOidcErrorMessage = null;

    function createOidcError(errorCode, errorDescription, statusCode) {
        const err = new Error(errorDescription || errorCode || "Erreur OIDC");
        err.name = "OidcError";
        err.code = errorCode || "unknown_error";
        err.description = errorDescription || "Erreur OIDC";
        err.status = statusCode || 0;
        return err;
    }

    function isFatalOidcError(err) {
        return Boolean(err && err.code && FATAL_OIDC_ERRORS.has(err.code));
    }

    function showOidcError(message) {
        let errorBox = document.getElementById("oidc-error-banner");
        if (!errorBox) {
            errorBox = document.createElement("div");
            errorBox.id = "oidc-error-banner";
            errorBox.style.maxWidth = "900px";
            errorBox.style.margin = "15px auto";
            errorBox.style.padding = "12px 14px";
            errorBox.style.border = "1px solid #fca5a5";
            errorBox.style.background = "#fef2f2";
            errorBox.style.color = "#991b1b";
            errorBox.style.borderRadius = "8px";
            errorBox.style.fontSize = "14px";
            const first = document.body.firstElementChild;
            if (first) {
                document.body.insertBefore(errorBox, first);
            } else {
                document.body.appendChild(errorBox);
            }
        }
        errorBox.innerHTML = `<strong>Erreur de configuration OIDC :</strong> ${message}`;
    }

    function markFatalOidcError(message) {
        fatalOidcErrorMessage = message;
        clearTokens();
        clearPkceState();
        showOidcError(message);
    }

    function normalizeBaseUrl(url) {
        return (url || "").replace(/\/+$/, "");
    }

    function getBrowserCrypto() {
        if (!window.crypto || typeof window.crypto.getRandomValues !== "function") {
            throw createOidcError(
                "browser_crypto_unavailable",
                "Votre navigateur ne supporte pas WebCrypto (crypto.getRandomValues)."
            );
        }
        return window.crypto;
    }

    function getRedirectUri() {
        const baseUri = normalizeBaseUrl(OIDC_BASE_URI || window.location.origin);
        return `${baseUri}/`;
    }

    function saveTokens(tokenResponse) {
        const expiresIn = Number(tokenResponse.expires_in || 0);
        const expiresAt = Date.now() + expiresIn * 1000;
        localStorage.setItem(ACCESS_TOKEN_KEY, tokenResponse.access_token || "");
        localStorage.setItem(EXPIRES_AT_KEY, String(expiresAt));

        if (tokenResponse.refresh_token) {
            localStorage.setItem(REFRESH_TOKEN_KEY, tokenResponse.refresh_token);
        }
    }

    function clearTokens() {
        localStorage.removeItem(ACCESS_TOKEN_KEY);
        localStorage.removeItem(REFRESH_TOKEN_KEY);
        localStorage.removeItem(EXPIRES_AT_KEY);
    }

    function getStoredTokenState() {
        const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
        const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
        const expiresAtRaw = localStorage.getItem(EXPIRES_AT_KEY);
        const expiresAt = expiresAtRaw ? Number(expiresAtRaw) : 0;
        return { accessToken, refreshToken, expiresAt };
    }

    function isAccessTokenValid(expiresAt, safetySeconds = TOKEN_REFRESH_SAFETY_SECONDS) {
        if (!expiresAt || Number.isNaN(expiresAt)) {
            return false;
        }
        return Date.now() + safetySeconds * 1000 < expiresAt;
    }

    function randomBytes(size = 32) {
        const bytes = new Uint8Array(size);
        const browserCrypto = getBrowserCrypto();
        browserCrypto.getRandomValues(bytes);
        return bytes;
    }

    function toBase64Url(input) {
        let bytes = input;
        if (input instanceof ArrayBuffer) {
            bytes = new Uint8Array(input);
        }
        let binary = "";
        for (let i = 0; i < bytes.length; i += 1) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
    }

    function randomString(size = 32) {
        return toBase64Url(randomBytes(size));
    }

    async function sha256Base64Url(value) {
        const browserCrypto = getBrowserCrypto();
        if (!browserCrypto.subtle || typeof browserCrypto.subtle.digest !== "function") {
            throw createOidcError(
                "browser_crypto_unavailable",
                "Votre navigateur ne supporte pas WebCrypto (crypto.subtle.digest)."
            );
        }
        const encoder = new TextEncoder();
        const digest = await browserCrypto.subtle.digest("SHA-256", encoder.encode(value));
        return toBase64Url(digest);
    }

    async function getOidcMetadata() {
        if (!oidcMetadataPromise) {
            try {
                const issuer = normalizeBaseUrl(OIDC_ISSUER_URL);
                oidcMetadataPromise = (async () => {
                    const response = await fetch(`${issuer}/.well-known/openid-configuration`);
                    if (!response.ok) {
                        throw createOidcError(
                            "invalid_oidc_configuration",
                            `Echec OIDC discovery (${response.status})`,
                            response.status
                        );
                    }
                    return response.json();
                })().catch((err) => {
                    oidcMetadataPromise = null;
                    if (isFatalOidcError(err)) {
                        throw err;
                    }
                    throw createOidcError(
                        "invalid_oidc_configuration",
                        `Impossible de recuperer la configuration OIDC depuis ${OIDC_ISSUER_URL}`,
                        0
                    );
                });
            } catch (_err) {
                throw createOidcError(
                    "invalid_oidc_configuration",
                    `Impossible de recuperer la configuration OIDC depuis ${OIDC_ISSUER_URL}`,
                    0
                );
            }
        }
        return oidcMetadataPromise;
    }

    async function redirectToLogin() {
        if (fatalOidcErrorMessage) {
            return;
        }
        const metadata = await getOidcMetadata();
        const codeVerifier = randomString(64);
        const codeChallenge = await sha256Base64Url(codeVerifier);
        const state = randomString(32);
        const nonce = randomString(32);

        sessionStorage.setItem(PKCE_VERIFIER_KEY, codeVerifier);
        sessionStorage.setItem(OIDC_STATE_KEY, state);
        sessionStorage.setItem(OIDC_NONCE_KEY, nonce);

        const params = new URLSearchParams({
            client_id: OIDC_CLIENT_ID,
            redirect_uri: getRedirectUri(),
            response_type: "code",
            scope: "openid email groups",
            state,
            nonce,
            code_challenge: codeChallenge,
            code_challenge_method: "S256",
        });

        window.location.href = `${metadata.authorization_endpoint}?${params.toString()}`;
    }

    function clearPkceState() {
        sessionStorage.removeItem(PKCE_VERIFIER_KEY);
        sessionStorage.removeItem(OIDC_STATE_KEY);
        sessionStorage.removeItem(OIDC_NONCE_KEY);
    }

    async function exchangeCodeForTokens(code, codeVerifier) {
        const metadata = await getOidcMetadata();
        const payload = new URLSearchParams({
            grant_type: "authorization_code",
            client_id: OIDC_CLIENT_ID,
            code,
            code_verifier: codeVerifier,
            redirect_uri: getRedirectUri(),
        });

        const response = await fetch(metadata.token_endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: payload.toString(),
        });

        if (!response.ok) {
            let errorCode = "token_exchange_failed";
            let errorDescription = `Echange du code OIDC impossible (${response.status})`;
            try {
                const responseData = await response.json();
                errorCode = responseData.error || errorCode;
                errorDescription = responseData.error_description || errorDescription;
            } catch (_err) {
                // Keep a generic message if response body is not JSON.
            }
            throw createOidcError(errorCode, errorDescription, response.status);
        }

        return response.json();
    }

    async function handleCallback() {
        const params = new URLSearchParams(window.location.search);
        const authError = params.get("error");
        if (authError) {
            clearTokens();
            clearPkceState();
            const authErrorDescription = params.get("error_description") || "Erreur renvoyee par le fournisseur OIDC.";
            window.history.replaceState({}, document.title, window.location.pathname);
            throw createOidcError(authError, authErrorDescription, 400);
        }

        const code = params.get("code");
        const returnedState = params.get("state");
        if (!code) {
            return false;
        }

        const expectedState = sessionStorage.getItem(OIDC_STATE_KEY);
        const codeVerifier = sessionStorage.getItem(PKCE_VERIFIER_KEY);
        if (!expectedState || !codeVerifier || returnedState !== expectedState) {
            clearTokens();
            clearPkceState();
            throw new Error("Etat OIDC invalide");
        }

        const tokenResponse = await exchangeCodeForTokens(code, codeVerifier);
        saveTokens(tokenResponse);
        clearPkceState();
        window.history.replaceState({}, document.title, window.location.pathname);
        return true;
    }

    async function refreshAccessToken() {
        const { refreshToken } = getStoredTokenState();
        if (!refreshToken) {
            clearTokens();
            return null;
        }

        const metadata = await getOidcMetadata();
        const payload = new URLSearchParams({
            grant_type: "refresh_token",
            client_id: OIDC_CLIENT_ID,
            refresh_token: refreshToken,
        });

        const response = await fetch(metadata.token_endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: payload.toString(),
        });

        if (!response.ok) {
            clearTokens();
            return null;
        }

        const tokenResponse = await response.json();
        saveTokens(tokenResponse);
        return tokenResponse.access_token || null;
    }

    async function getValidAccessToken() {
        const { accessToken, expiresAt } = getStoredTokenState();
        if (accessToken && isAccessTokenValid(expiresAt)) {
            return accessToken;
        }

        if (!refreshPromise) {
            refreshPromise = refreshAccessToken().finally(() => {
                refreshPromise = null;
            });
        }

        return refreshPromise;
    }

    const rawFetch = window.fetch.bind(window);
    window.fetch = async function(url, options = {}) {
        const isRelativeUrl = typeof url === "string" && url.startsWith("/");
        let authOptions = options;

        if (isRelativeUrl) {
            const token = await getValidAccessToken();
            if (token) {
                authOptions = {
                    ...options,
                    headers: {
                        ...options.headers,
                        Authorization: `Bearer ${token}`,
                    },
                };
            }
        }

        const response = await rawFetch(url, authOptions);
        if (isRelativeUrl && response.status === 401 && !fatalOidcErrorMessage) {
            clearTokens();
            setTimeout(() => {
                redirectToLogin();
            }, 400);
        }
        return response;
    };

    (async function initOidc() {
        try {
            await handleCallback();
            const token = await getValidAccessToken();
            if (!token) {
                await redirectToLogin();
            }
        } catch (err) {
            if (isFatalOidcError(err)) {
                markFatalOidcError(err.description || "Erreur OIDC non recuperable.");
                return;
            }

            clearTokens();
            clearPkceState();
            setTimeout(() => {
                redirectToLogin();
            }, 400);
        }
    })();
})();
