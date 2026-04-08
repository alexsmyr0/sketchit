from django.test import TestCase

class RoomEntryPageTests(TestCase):
    def test_room_entry_page_returns_200(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "rooms/room_entry.html")
        self.assertContains(response, '<form id="join-form"')
        self.assertContains(response, '<form id="create-form"')
