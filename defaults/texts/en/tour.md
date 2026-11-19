## Tour

- A line starting with a slash is a command, e.g. `/help`. Type `/` and a menu of commands opens; ↑/↓ pick one, Tab or → takes it, Enter runs it, Esc closes the menu. After a command the menu offers its options.
- Anything else is a conversation with me.
- `/help` lists the commands, `/help <command>` shows its options.
- `/settings` shows and changes settings (language, provider, model).
- `/setup` runs the model setup again.
- `/tour` shows this tour any time.
- `/add-information` is a conversation about you: tell me in your own words what you want and what you can do, and put file paths and links (`.txt`, `.md`, `.pdf`, `.html`, web pages) anywhere in a message. I ask about what is still missing or only partly known. Type `done` or press Ctrl-D to save and finish; Ctrl-C cancels the round. `/add-information --source <file|url>` adds sources without a conversation.
- `/show-profile` shows what I know about you (missing, partly known or known, and where I learned it), your sources, the generated filter file and where each file lives.
- Web pages and `.html` files pass an injection check first; anything suspicious goes into an encrypted quarantine. `/show-quarantine` lists it, `/release-quarantine <id>` lets it in after your yes.
- `/exit` or Ctrl-D quits. Ctrl-C cancels the current line or the running command.
- Commands also run without the chat: `sortingcap run <command>`, e.g. from cron. `setup` is chat-only.

## Next steps

- Start with `/add-information`: a short conversation where you tell me about yourself and give me your CV (its path or link in any message will do). Then check the result with `/show-profile`.
- Coming versions will go through job listings (`/scrape-jobs`) and write letters.
