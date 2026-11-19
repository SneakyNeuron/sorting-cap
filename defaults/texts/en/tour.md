## Tour

- A line starting with a dash is a command, e.g. `-help`. Tab completes command names.
- Anything else is a conversation with me.
- `-help` lists the commands, `-help <command>` shows its options.
- `-settings` shows and changes settings (language, provider, model).
- `-setup` runs the model setup again.
- `-tour` shows this tour any time.
- `-add-information` tells me about you: files and links (`.txt`, `.md`, `.pdf`, `.html`, web pages), a short introduction and my questions on what is still missing. `-add-information --source <file|url>` adds sources without questions.
- `-show-profile` shows what I know about you, your sources, the generated filter file and where each file lives.
- Web pages and `.html` files pass an injection check first; anything suspicious goes into an encrypted quarantine. `-show-quarantine` lists it, `-release-quarantine <id>` lets it in after your yes.
- `-exit` or Ctrl-D quits. Ctrl-C cancels the current line or the running command.
- Commands also run without the chat: `sortingcap run <command>`, e.g. from cron. `setup` is chat-only.

## Next steps

- Start with `-add-information`: give me your CV and answer a few questions. Then check the result with `-show-profile`.
- Coming versions will go through job listings (`-scrape-jobs`) and write letters.
