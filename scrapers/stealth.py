"""
Stealth and anti-detection utilities for Playwright.
Evasion scripts are evaluated on new document creation to mask automation footprints.
"""

import random

# Real modern desktop user-agents
COMMON_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

COMMON_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
]


def get_random_user_agent() -> str:
    """Returns a random modern desktop User-Agent."""
    return random.choice(COMMON_USER_AGENTS)


def get_random_viewport() -> dict:
    """Returns a realistic viewport dictionary."""
    return random.choice(COMMON_VIEWPORTS)


# Advanced evasion JavaScript script evaluated before any page script executes
STEALTH_EVASION_SCRIPT = """
(() => {
    // 1. Remove navigator.webdriver completely
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Mock Chrome runtime object
    if (!window.chrome) {
        window.chrome = {};
    }
    window.chrome.runtime = {
        connect: () => {},
        sendMessage: () => {},
        onMessage: { addListener: () => {} }
    };
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
    };

    // 3. Spoof plugins array to simulate realistic PDF / Chrome plugins
    const mockPlugins = [
        { name: "Chrome PDF Plugin", filename: "internal-pdf-viewer", description: "Portable Document Format" },
        { name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai", description: "" },
        { name: "Native Client", filename: "internal-nacl-plugin", description: "" }
    ];
    Object.defineProperty(navigator, 'plugins', {
        get: () => mockPlugins,
        configurable: true
    });

    // 4. Spoof languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en'],
        configurable: true
    });

    // 5. Spoof hardware concurrency & memory
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true
    });

    // 6. Spoof WebGL & WebGL2 Vendor and Renderer consistent with OS platform
    const isWindows = navigator.userAgent.includes('Windows') || navigator.platform.includes('Win');
    const isMac = navigator.userAgent.includes('Macintosh') || navigator.platform.includes('Mac');
    
    let glVendor = 'Google Inc. (NVIDIA)';
    let glRenderer = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080, OpenGL 4.5.0)';
    if (isWindows) {
        glRenderer = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)';
    } else if (isMac) {
        glVendor = 'Apple';
        glRenderer = 'Apple M1 Pro';
    }

    const patchGL = (proto) => {
        if (!proto) return;
        const originalGetParameter = proto.getParameter;
        proto.getParameter = function(parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) {
                return glVendor;
            }
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) {
                return glRenderer;
            }
            return originalGetParameter.apply(this, arguments);
        };
    };

    patchGL(window.WebGLRenderingContext ? WebGLRenderingContext.prototype : null);
    patchGL(window.WebGL2RenderingContext ? WebGL2RenderingContext.prototype : null);

    // 7. Notification permissions spoofing
    if (window.Notification) {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    }
})();
"""
