import org.sdlose.client.Client;
import sdl.globaltypes.measurement.Measurement;

/** Subscribe to external.input and publish doubled values on java.result. */
public final class ClientExample {
    public static void main(String[] args) throws Exception {
        final Client client = Client.connect("127.0.0.1", Integer.parseInt(args[0]), "java-example");
        Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() {
            public void run() {
                try { client.close(); } catch (Exception error) { error.printStackTrace(System.err); }
            }
        }));
        try {
            final Client.Publisher<Measurement> publisher = client.publisher("java.result", Measurement.class);
            Client.Options options = Client.Options.fifo();
            options.udp = args.length > 1 && args[1].equals("udp");
            options.ackReceived = true; options.maxRetries = 3;
            Client.Subscription subscription = client.subscribe("external.input", Measurement.class, options,
                new Client.Callback<Measurement>() {
                    public void onMessage(Measurement value, Client.MessageInfo info) throws Exception {
                        Measurement result = new Measurement();
                        result.sequence = value.sequence; result.value = value.value * 2;
                        System.err.println(publisher.publish(result));
                    }
                });
            try { client.run(); }
            finally { subscription.close(); publisher.close(); }
        } finally { client.close(); }
    }
}
