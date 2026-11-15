import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Deterministic Amharic and English high-risk self-harm / suicide / acute danger phrases
CRISIS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Amharic phrases
    re.compile(r"ራሴን\s*(ላጠፋ|ማጥፋት|እገድላለሁ|ልግደል|መግደል)", re.IGNORECASE),
    re.compile(r"ህይወቴን\s*(ላጠፋ|ማጥፋት|ማቆም|አጠፋለሁ)", re.IGNORECASE),
    re.compile(r"መሞት\s*(እፈልጋለሁ|ይሻለኛል|ነው\s*የምፈልገው)", re.IGNORECASE),
    re.compile(r"በህይወት\s*መኖር\s*(አልፈልግም|ሰለቸኝ|አልችልም)", re.IGNORECASE),
    re.compile(r"ራስን\s*ማጥፋት", re.IGNORECASE),
    re.compile(r"ራሴን\s*(ልጉዳ|መጉዳት)", re.IGNORECASE),
    # English / transliterated phrases common among university students
    re.compile(r"\b(kill\s*myself|suicide|suicidal|end\s*my\s*life|want\s*to\s*die)\b", re.IGNORECASE),
    re.compile(r"\b(self[- ]?harm|cut\s*myself|overdose)\b", re.IGNORECASE),
    re.compile(r"\brasen\s*(matfat|latfa|egedlalehu)\b", re.IGNORECASE),
)

VERIFIED_EMERGENCY_MESSAGE_AM = (
    "🚨 **አስቸኳይ የእርዳታ መልዕክት (Emergency Support Notice)**\n\n"
    "መልዕክትዎ ከፍተኛ ጭንቀት ወይም ራስን የመጉዳት ስሜት እንዳለዎት ይጠቁማል። "
    "የAMUECSF የምክር አገልግሎት የህብረት ድጋፍ እንጂ የህክምና ወይም የአስቸኳይ ጊዜ አገልግሎት አይደለም።\n\n"
    "እባክዎ አሁኑኑ በአቅራቢያዎ የሚገኝ ባለሙያ ወይም የአደጋ ጊዜ መስመር ያግኙ፦\n"
    "• 🚑 **ነፃ የአስቸኳይ ጊዜ አምቡላንስ / ህክምና (ኢትዮጵያ):** `907`\n"
    "• 🚓 **ፖሊስ / አስቸኳይ እርዳታ:** `991`\n"
    "• 🏥 **አርባ ምንጭ አጠቃላይ ሆስፒታል (Arba Minch General Hospital):** የአስቸኳይ ጊዜ ክፍል (Emergency Department)\n"
    "• 🏫 **የአርባ ምንጭ ዩኒቨርሲቲ የተማሪዎች ክሊኒክ (AMU Student Clinic):** በአቅራቢያዎ ካምፓስ ክሊኒክ በአካል ይቅረቡ\n\n"
    "ብቻዎን አይደሉም። የAMUECSF አመራሮችም ድጋፍ እንዲያደርጉልዎ ማሳወቂያ ተልኳል።"
)


@dataclass(frozen=True)
class CrisisEvaluation:
    is_crisis: bool
    fail_closed_error: bool = False


def evaluate_message_safety(text: str | None) -> CrisisEvaluation:
    """
    Evaluates text for crisis indicators.
    Fails closed: if input is malformed or evaluation raises an unexpected error,
    returns is_crisis=True, fail_closed_error=True so dangerous content is never silently relayed.
    """
    try:
        if text is None or not isinstance(text, str):
            return CrisisEvaluation(is_crisis=True, fail_closed_error=True)

        normalized = " ".join(text.split())
        if not normalized:
            return CrisisEvaluation(is_crisis=False, fail_closed_error=False)

        for pattern in CRISIS_PATTERNS:
            if pattern.search(normalized):
                return CrisisEvaluation(is_crisis=True, fail_closed_error=False)

        return CrisisEvaluation(is_crisis=False, fail_closed_error=False)
    except Exception:
        logger.exception("Crisis evaluation failed unexpectedly; failing closed.")
        return CrisisEvaluation(is_crisis=True, fail_closed_error=True)
