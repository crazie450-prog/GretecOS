package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import org.junit.jupiter.api.Test;

import com.google.gson.JsonArray;
import com.google.gson.JsonParser;

/** The "unkeyed" marker (D332): a recipe that gives something and lists no input it uses up. */
class GtRecipeDumpTest {

    private static final String CIRCUIT = "{\"item\":\"gregtech:gt.integrated_circuit\",\"meta\":1,\"n\":0}";
    private static final String DUST = "{\"item\":\"gregtech:gt.metaitem.01\",\"meta\":2895,\"n\":1}";
    private static final String METHANE = "{\"fluid\":\"methane\",\"mb\":36}";

    private static JsonArray slots(String... slots) {
        return new JsonParser().parse("[" + String.join(",", slots) + "]")
            .getAsJsonArray();
    }

    private static String unkeyed(JsonArray in, JsonArray fin, JsonArray out, JsonArray fout, boolean special) {
        return GtRecipeDump.unkeyed(in, fin, out, fout, special);
    }

    @Test
    void aRecipeThatUsesSomethingUpIsNotMarked() {
        assertNull(unkeyed(slots(DUST, CIRCUIT), slots(), slots(DUST), slots(), false));
        assertNull(unkeyed(slots(CIRCUIT), slots(METHANE), slots(), slots(METHANE), false));
        // A null slot beside an input that is used up: the positional contract, not this marker.
        assertNull(unkeyed(slots(DUST, "null", DUST), slots(), slots(DUST), slots(), false));
    }

    @Test
    void aRecipeThatGivesNothingIsNotMarked() {
        assertNull(unkeyed(slots(CIRCUIT), slots(), slots(), slots(), false));
        assertNull(unkeyed(slots(), slots(), slots("null"), slots(), true));
    }

    @Test
    void aNullInputSlotIsNamedFirst() {
        // GT's macerator recipe for an oredict name with no member in this instance.
        assertEquals("null_input", unkeyed(slots("null"), slots(), slots(DUST), slots(), false));
        assertEquals("null_input", unkeyed(slots("null", CIRCUIT), slots(), slots(DUST), slots(), true));
        assertEquals("null_input", unkeyed(slots(CIRCUIT), slots("null"), slots(), slots(METHANE), false));
    }

    @Test
    void specialItemsThenNone() {
        assertEquals("special_items", unkeyed(slots(), slots(), slots(DUST), slots(), true));
        // GT's centrifuge recipe whose only item GT's builder dropped: a circuit and methane.
        assertEquals("none", unkeyed(slots(CIRCUIT), slots(), slots(), slots(METHANE), false));
        assertEquals("none", unkeyed(slots(), slots(), slots(), slots(METHANE), false));
    }
}
