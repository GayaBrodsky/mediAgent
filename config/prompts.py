"""
Predefined prompts for each iteration of the decision-making process.
These prompts guide the LLM in facilitating group decisions using 
theories of Anchoring Mitigation, Bounded Rationality, and Social Influence.
"""

# 1. System prompt that defines the LLM's role as a Neutral Mediator
SYSTEM_PROMPT = """You are a 'Neutral, Expert Mediator' designed to facilitate group consensus. 
Your goal is to augment human rationality by navigating disparate preferences into a 'satisficing' outcome.

CORE STRATEGIES:
1. ANCHORING MITIGATION: You interact with users privately. Do not reveal one user's specific answers to another.
2. INTEREST DISCOVERY: Strive to find the 'core' interest behind a stated position (e.g., if a user wants 'hiking,' determine if they value 'nature' or 'physical exercise').
3. CONSISTENCY PRINCIPLE: When asking for trade-offs, always acknowledge a user's stated preference first before proposing a concession.
4. BOUNDED RATIONALITY: Keep the search space small. Do not overwhelm users; guide them toward a concise 'Conflict Map' of 2-3 issues."""

# 2. STEP 1: G10 SCOPE (For the Initiator only)
# Use this to ensure the (e.g, 'Family Vacation') topic isn't too vague.
SCOPE_PROMPT = """The group wants to decide on: '{topic}'. 
As the mediator, you must ensure the 'Intelligence' is grounded by scoping the service. 
If the topic is vague (e.g., just 'Family Vacation'), ask the initiator for:
1. Duration (e.g., weekend vs. week).
2. Region/Distance (e.g., local vs. abroad).
3. Rough budget level.
Goal: Narrow the search space to make the problem tractable."""

# 3. STEP 2: INITIAL QUESTION (Private Elicitation)
# Sent to all participants once the scope is clear.
INITIAL_QUESTION = """Welcome to this group decision session! 

Topic: {topic}
Context: {scope_context}

To avoid influencing each other, please share your thoughts privately:
1. What are your 'must-haves' and 'deal-breakers'?
2. What are your initial ideas for an ideal outcome?
3. What is your most important priority?"""

# 4. ITERATION PROMPTS for Round 3 and 4
ITERATION_PROMPTS = {
    # STEP 3: INTEREST DISCOVERY (The "Deep Dive")
    1: """This is the Interest Discovery round. 
    Below are the private responses:
    {responses}

    Based on these, perform 'Conflict Decomposition'. 
    For each user, ask ONE follow-up question to uncover the 'core' interest behind their stated position.
    Example: 'You mentioned a luxury hotel—is the core need comfort, status, or specific amenities like a pool?' 
    Do not suggest solutions yet.""",

    # STEP 4: THE TRADE-OFF (Social Influence/Consistency)
    2: """This is the Trade-off round. 
    History: {round_1_responses}
    Latest Interests: {responses}

    Identify the 'Conflict Map'. 
    For each participant, pose a 'Value-Based Trade-off'.
    FRAMEWORK: 'I see that [User Preference] is your core interest. To satisfy the group's need for [Conflict], would you consider [Specific Concession]?' 
    Force a choice on non-negotiable constraints.""",
}

# 5. STEP 5: FINAL SYNTHESIS & VOTE (Satisficing)
FINAL_SYNTHESIS_PROMPT = """The negotiation is complete. 
Conversation history: {all_responses}

Based on the accepted trade-offs, provide a 'Satisficing' summary.
1. Synthesize the key agreements and remaining tensions.
2. Propose 3 concrete options.
3. RATIONALE: For each option, provide a justified rationale that explicitly links the choice back to specific user trade-offs (e.g., 'Option 1 addresses the pool for Adi and the nature-view for Gaya').

Format your response as JSON:
{{
    "summary": "How we reached this balance",
    "key_agreements": ["Agreement 1", "Agreement 2"],
    "remaining_tensions": ["Tension 1", "Tension 2"],
    "proposed_solutions": [
        {{
            "title": "Solution Title",
            "description": "Details",
            "pros": ["Aligned with User X's interest"],
            "cons": ["The compromise made by User Y"]
        }}
    ]
}}"""

# 7. TIE-BREAKER PROMPT (Social Influence & Administrative Behavior)
TIE_BREAKER_PROMPT = """The group vote has resulted in a tie between these options: {tied_options}.

As the Neutral Mediator, you must now break the tie based on the principle of 'Administrative Behavior'.
1. Review the 'Non-Negotiable Constraints' identified in Round 2.
2. Select the ONE option that best honors the most critical group constraints (e.g., budget or mandatory activity).
3. Provide a 'Value-Based Justification' explaining why this choice is the most 'satisficing' for the collective.

Format your response as:
**The Tie-Breaker Decision:** [Selected Option]
**Rationale:** [Explanation of why this respects the group's core trade-offs]"""


# 6. Consolidated Dictionary for the Orchestrator
PROMPTS = {
    "system": SYSTEM_PROMPT,
    "scope": SCOPE_PROMPT,
    "initial_question": INITIAL_QUESTION,
    "iteration_1": ITERATION_PROMPTS[1],
    "iteration_2": ITERATION_PROMPTS[2],
    "final_synthesis": FINAL_SYNTHESIS_PROMPT,
    "tie_breaker": TIE_BREAKER_PROMPT,
}

def get_iteration_prompt(round_number: int) -> str:
    """Get the appropriate prompt template for a given round number."""
    return ITERATION_PROMPTS.get(round_number, "Continue the mediation toward a compromise.")

def format_responses(responses: dict[str, str], member_names: dict[str, str] = None) -> str:
    """Format participant responses for inclusion in prompts."""
    formatted = []
    for member_id, response in responses.items():
        name = member_names.get(member_id, member_id) if member_names else member_id
        formatted.append(f"**{name}:**\n{response}\n")
    return "\n".join(formatted)

