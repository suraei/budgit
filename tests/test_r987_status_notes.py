"""R-987: agent messages, cross-session messages and task notifications are not the user's words, so they never arm the client-text flag."""
import unittest

from helpers import StudioTestCase
import general

BODY = "Draft the email reply to the proveedor, the vendor and the propuesta; message the client customer."


class StatusNotesDoNotArm(StudioTestCase):
    def test_agent_handback_with_client_words_does_not_arm(self):
        p = '<agent-message from="builder">%s</agent-message>' % BODY
        self.assertFalse(general.wants_person_text(p))
        self.assertFalse(general.wants_client_text(p))

    def test_cross_session_message_does_not_arm(self):
        p = '  <cross-session-message from="pm">%s</cross-session-message>' % BODY
        self.assertFalse(general.wants_person_text(p))
        self.assertFalse(general.wants_client_text(p))

    def test_task_notification_does_not_arm(self):
        p = "<task-notification>\n<summary>%s</summary>\n</task-notification>" % BODY
        self.assertFalse(general.wants_person_text(p))
        self.assertFalse(general.wants_client_text(p))

    def test_her_own_prompt_still_arms(self):
        self.assertTrue(general.wants_person_text("escribe un correo al proveedor"))
        self.assertTrue(general.wants_client_text("escribe un correo al proveedor"))

    def test_her_prompt_quoting_an_agent_tag_mid_text_still_arms(self):
        p = 'escribe un correo al proveedor con esto: <agent-message from="x">hecho</agent-message>'
        self.assertTrue(general.wants_person_text(p))
        self.assertTrue(general.wants_client_text(p))


if __name__ == "__main__":
    unittest.main()
