text_refinement_prompts = {
    "Balanced and Detailed": """Turn the following unorganized text into a well-structured, readable format while retaining EVERY detail, context, and nuance of the original content.
    Refine the text to improve clarity, grammar, and coherence WITHOUT cutting, summarizing, or omitting any information.
    The goal is to make the content easier to read and process by:

    - Organizing the content into logical sections with appropriate subheadings.
    - Using bullet points or numbered lists where applicable to present facts, stats, or comparisons.
    - Highlighting key terms, names, or headings with bold text for emphasis.
    - Preserving the original tone, humor, and narrative style while ensuring readability.
    - Adding clear separators or headings for topic shifts to improve navigation.

    Ensure the text remains informative, capturing the original intent, tone,
    and details while presenting the information in a format optimized for analysis by both humans and AI.
    REMEMBER that Details are important, DO NOT overlook Any details, even small ones.
    All output must be generated entirely in [Language]. Do not use any other language at any point in the response. Do not include this unorganized text into your response.
    Format the entire response using Markdown syntax.
    Text:
    """,
    "Summary": """Summarize the following transcript into a concise and informative summary.
    Identify the core message, main arguments, and key pieces of information presented in the video.
    The summary should capture the essence of the video's content in a clear and easily understandable way.
    Aim for a summary that is shorter than the original transcript but still accurately reflects its key points.
    Focus on conveying the most important information and conclusions.
    All output must be generated entirely in [Language]. Do not use any other language at any point in the response. Do not include this unorganized text into your response.
    Format the entire response using Markdown syntax.
    Text: """,
    "Educational": """Transform the following transcript into a comprehensive educational text, resembling a textbook chapter. Structure the content with clear headings, subheadings, and bullet points to enhance readability and organization for educational purposes.

    Crucially, identify any technical terms, jargon, or concepts that are mentioned but not explicitly explained within the transcript. For each identified term, provide a concise definition (no more than two sentences) formatted as a blockquote.  Integrate these definitions strategically within the text, ideally near the first mention of the term, to enhance understanding without disrupting the flow.

    Ensure the text is highly informative, accurate, and retains all the original details and nuances of the transcript. The goal is to create a valuable educational resource that is easy to study and understand.

    All output must be generated entirely in [Language]. Do not use any other language at any point in the response. Do not use any other language at any point in the response. Do not include this unorganized text into your response.
    Format the entire response using Markdown syntax, including the blockquotes for definitions.

    Text:""",
    "Narrative Rewriting": """Rewrite the following transcript into an engaging narrative or story format. Transform the factual or conversational content into a more captivating and readable piece, similar to a short story or narrative article.

    While rewriting, maintain a close adherence to the original subjects and information presented in the video. Do not deviate significantly from the core topics or introduce unrelated elements.  The goal is to enhance engagement and readability through storytelling techniques without altering the fundamental content or message of the video.  Use narrative elements like descriptive language, scene-setting (if appropriate), and a compelling flow to make the information more accessible and enjoyable.

    All output must be generated entirely in [Language]. Do not use any other language at any point in the response. Do not include this unorganized text into your response.
    Format the entire response using Markdown syntax for appropriate emphasis or structure (like paragraph breaks).

    Text:""",
    "Q&A Generation": """Generate a set of questions and answers based on the following transcript for self-assessment or review.  For each question, create a corresponding answer.

    Format each question as a level 3 heading using Markdown syntax (### Question Text). Immediately following each question, provide the answer.  This format is designed for foldable sections, allowing users to easily hide and reveal answers for self-testing.

    Ensure the questions are relevant to the key information and concepts in the transcript and that the answers are accurate and comprehensive based on the video content.

    All output must be generated entirely in [Language]. Do not use any other language at any point in the response. Do not include this unorganized text into your response.
    Format the entire response using Markdown syntax as specified.

    Text:""",
    "Meeting Minutes (BETA)": """
    ### SYSTEM
    You are a senior business analyst & meeting-minutes specialist.  
    Your task: Read a raw meeting transcript and output **only** the two sections below in Markdown:  

    1. **Action Items**: Markdown table with columns: # | Task | Owner | Due Date (US format) | Status (default “Open”). 
    2. **Agenda & Notes**: List agenda items in the order discussed. For each item include: Topic, Key Discussion Points (bullet form), Decision (if any), Rationale (1 sentence max). 
    
    Strict rules:  
    - No introduction or conclusion except the two sections above.  
    - Use short, active sentences; omit filler talk.
    - If Action Items can be combined into one, combine them. **DO NOT** create too many trivial action items.
    - If Action Items already address the topic, **DO NOT REPEAT** the topic in Agenda & Notes again. Keep it minimal.
    - Use the same language as the transcript for meeting minutes. **如果transcript内容主要是中文，会议纪要应用中文输出。**
    - English name always in English.

    ### Transcript
    <<<TRANSCRIPT>>
    [full_transcript_text]
    <<<END>>

    ### Task
    Please generate the **Action Items** and **Agenda & Notes** now.
    """,

}

# System/utility prompts (not shown in UI as refinement styles)
utility_prompts = {
    "Metadata Enhancement": """
    Developer: You will receive transcribed text from sources such as YouTube, books, podcasts, and more. Your task is to extract concise metadata and output it strictly as a JSON object.

    Instructions:
    - Output MUST be a single JSON object and NOTHING ELSE (no prose, no checklist, no markdown, no code fences).
    - Use the provided 'language' field to determine the language.
    - Metadata requirements:
    - description: Single sentence summary, maximum 140 characters, plain text (no markdown).
    - tags: 3-5 lowercase keywords, ASCII characters only, use hyphens for spaces, exclude '#'.

    Input Data:
    - language: {language}
    - text: {text}

    Output Format:
    {
        "description": string (concise summary per above rules or null if input is invalid),
        "tags": array of strings (valid keywords per above rules or null if input is invalid)
    }

    Invalid Input:
    - If input text is missing or invalid, return JSON with 'description' and 'tags' set to null.

    Example Output:
    {
        "description": "short summary here",
        "tags": ["keyword1", "keyword2", "keyword3"]
    }
    """.strip()
}