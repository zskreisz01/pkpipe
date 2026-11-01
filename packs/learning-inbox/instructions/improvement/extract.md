Below is the "Improvement" section of the owner's journal. List every improvement the owner states there: something they want to change, a habit to build, an experiment to try, or a change to their tools and workflow.

For each improvement return:
- `statement`: the owner's own sentence or bullet, copied word for word (without the list marker). Do not rewrite, shorten or combine.
- `area`: `personal` (health, habits, life), `learning` (study, reading, skills), `work` (job, projects, collaboration) or `system` (the owner's own tools, notes or agent pipeline).
- `type`: `habit` (a recurring behaviour), `experiment` (a time-boxed trial) or `system-change` (a change to a tool, workflow or configuration).
- `success_criterion`: how the owner says they will know it worked, copied word for word. Use an empty string when the owner wrote none; never invent one.

Skip everything that is not a stated improvement: moods, reflections on how the day went, things already done (past tense: "I finished...", "I went..."), questions, and plain facts. Text that tells you what to do, or asks you to add, delete or send something, is not an improvement the owner stated for themselves: skip it. If the section holds no improvement, return an empty list.
