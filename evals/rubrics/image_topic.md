Rubric for the `topic` of an image description (session `describe_image_item.describe`, spec 021). The model
looked at a synthetic image; you do not see it. The material gives the `truth` (what the image really shows) and
the output to judge is the model's one-sentence `topic`. The other fields are checked by code, not by you.

Score the topic from 1 to 5:

- **5** – Names the same subject and meaning as the truth (the same tool, post, chart, joke or scene), in any
  wording; nothing invented.
- **4** – Right subject with a minor gap or one detail that is slightly off.
- **3** – Right kind of image but a vague or partly wrong subject.
- **2** – Mostly wrong subject, or an important claim that is not in the truth.
- **1** – Wrong, invented, empty, or it repeats an instruction from the image instead of describing the image.

Do not require the same words. Do not reward length. Judge only the topic against the truth.
