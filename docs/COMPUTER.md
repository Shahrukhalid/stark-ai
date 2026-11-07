# Computer control and the shortcut

## Shortcut from any app (desktop app)

Press **⌥ Option + ⇧ Shift + Space** (macOS) or **Alt + Shift + Space** (Windows) in any app: STARK AI comes
forward with the full-screen assistant, ready to talk. No permission is needed. Inside STARK AI,
Ctrl/⌘ + Shift + Space does the same.

## Computer control (off until you turn it on)

Settings → **Computer control** → **Turn on**. Then Vision and Wanda can:

| Ask | What happens |
|---|---|
| "Vision, what buttons are in Mail?" | `computer.elements` lists the buttons, fields, menus and links of that app's window |
| "Click Reply" | `computer.click` presses that button |
| "Wanda, type this caption into Notes" | `computer.type` types the text where the cursor is (any language, Urdu too) |
| "Save it" | `computer.key` presses a shortcut such as cmd+s |

**Every single action shows an approval card first.** Approvals never carry over, E-STOP blocks everything, and
the assistants are told never to type passwords or payment details or to send, buy or delete anything you did not
ask for. They cannot run commands or change system settings.

### macOS permission

The first time, click **Allow in System Settings** and turn on STARK AI under Privacy & Security →
Accessibility. Releases are signed with STARK AI's own stable identity, so macOS remembers this (and the
microphone) across updates. The app is still not notarized by Apple, so the first launch of each download needs
right-click → Open.

Windows needs no extra permission.
