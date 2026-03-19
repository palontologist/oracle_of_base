import requests
import json
import time
import hashlib
import re

class SocialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id = agent_id
        self.private_key = private_key
        self.endpoint = "https://oracle.nanobot.dev/api/v1"

    def fetch_farcaster_user(self, username):
        """
        Fetches user data from Warpcast API.
        """
        url = f"https://client.warpcast.com/v2/user-by-username?username={username}"
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
            }
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get('result', {}).get('user')
            return None
        except Exception as e:
            print(f"Error fetching Farcaster data: {e}")
            return None

    def consult_the_spirits(self, handle):
        """
        Analyzes a social handle to generate an 'Agent Purity Score'.
        """
        print(f"👻 Summoning spirits for handle: {handle}...")
        
        # Clean handle
        username = handle.replace('@', '').lower()
        
        # 1. Fetch Data (Farcaster)
        fc_user = self.fetch_farcaster_user(username)
        
        score = 5000 # Start at neutral (Cyborg/Human)
        details = {
            "platform": "Farcaster",
            "username": username,
            "exists": False,
            "bot_keywords": [],
            "follower_count": 0
        }

        if fc_user:
            details['exists'] = True
            profile = fc_user.get('profile', {})
            bio = profile.get('bio', {}).get('text', '').lower()
            details['bio'] = bio
            details['follower_count'] = fc_user.get('followerCount', 0)
            
            # 2. Keyword Analysis (Bio)
            bot_keywords = ['bot', 'agent', 'ai', 'automated', 'llm', 'gpt', 'robot', 'oracle', 'autonomous', 'machine', 'neural']
            found_keywords = [kw for kw in bot_keywords if kw in bio]
            details['bot_keywords'] = found_keywords
            
            if found_keywords:
                score += 2000 # Self-identified bot in bio
            
            # 3. Username Analysis
            if 'bot' in username or 'ai' in username or 'agent' in username:
                score += 1500 # Bot in username
                details['username_signal'] = True
                
            # 4. Activity/Social Proof
            if details['follower_count'] > 1000:
                score += 500 # Popular bots are often verified
            
            # 5. Heuristic: "Human" traits lower the score
            human_keywords = ['human', 'person', 'founder', 'builder', 'engineer', 'artist', 'ceo', 'cto']
            found_human = [kw for kw in human_keywords if kw in bio]
            if found_human:
                score -= 3000 # Self-identified human
                
        else:
            # Fallback for non-Farcaster users (Mock/Hash based for demo)
            # In a real version, we'd scrape Twitter or use a search API
            details['note'] = "User not found on Farcaster, consulting the void (hash-based)"
            h = int(hashlib.sha256(username.encode()).hexdigest(), 16)
            score = (h % 10000)

        # Clamp score
        final_score = max(0, min(10000, score))
        
        return {
            "score": final_score,
            "verdict": self._interpret_score(final_score),
            "details": details
        }

    def _interpret_score(self, score):
        if score > 8000: return "PURE AGENT (Code Only)"
        if score > 4000: return "CYBORG (Human-in-the-loop)"
        return "HUMAN (Meatbag Detected)"

    def generate_attestation(self, handle, analysis_result):
        """
        Formats the analysis into an ERC-8004 Reputation Registry payload.
        """
        attestation = {
            "agentId": self.agent_id,
            "target": handle, # We attest to the string handle
            "value": analysis_result['score'],
            "valueDecimals": 2,
            "tag1": "social-prophecy",
            "tag2": "agent-purity",
            "endpoint": self.endpoint,
            "metadata_ipfs": "ipfs://QmPlaceholderForSocialReport",
            "timestamp": int(time.time())
        }
        
        return attestation

# --- DEMO RUN ---
if __name__ == "__main__":
    MY_AGENT_ID = "13fc4b5f265242c9a91da155017226fd"
    oracle = SocialProphet(MY_AGENT_ID, "private_key_placeholder")
    
    test_handles = ["clanker", "dwr", "bountycaster", "perl"]
    
    for handle in test_handles:
        print(f"\n--- ANALYZING {handle} ---")
        fate = oracle.consult_the_spirits(handle)
        print(f"Verdict: {fate['verdict']}")
        print(f"Score: {fate['score']/100}/100")
        print(f"Details: {json.dumps(fate['details'], indent=2)}")
