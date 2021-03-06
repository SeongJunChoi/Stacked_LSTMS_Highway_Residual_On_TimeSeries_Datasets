from HAR_data_handler import get_HAR_data
import tensorflow as tf
from sklearn import metrics
from sklearn.utils import shuffle
import numpy as np
from base_config import Config, YaxisBundle, PlotUtil
from mnist_data_handler import get_mnist_data_with_time_series_size

def one_hot(y):
    """convert label from dense to one hot
      argument:
        label: ndarray dense label ,shape: [sample_num,1]
      return:
        one_hot_label: ndarray  one hot, shape: [sample_num,n_class]
    """
    # e.g.: [[5], [0], [3]] --> [[0, 0, 0, 0, 0, 1], [1, 0, 0, 0, 0, 0], [0, 0, 0, 1, 0, 0]]

    y = y.reshape(len(y))
    n_values = np.max(y) + 1
    return np.eye(n_values)[np.array(y, dtype=np.int32)]  # Returns FLOATS

def apply_batch_norm(input_tensor, config, i):

    with tf.variable_scope("batch_norm") as scope:
        if i != 0 :
            # Do not create extra variables for each time step
            scope.reuse_variables()

        # Mean and variance normalisation simply crunched over all axes
        axes = list(range(len(input_tensor.get_shape())))

        mean, variance = tf.nn.moments(input_tensor, axes=axes, shift=None, name=None, keep_dims=False)
        stdev = tf.sqrt(variance + 0.001)

        # Rescaling
        bn = input_tensor - mean
        bn /= stdev
        # Learnable extra rescaling

        # tf.get_variable("relu_fc_weights", initializer=tf.random_normal(mean=0.0, stddev=0.0)
        bn *= tf.get_variable("a_noreg", initializer=tf.random_normal([1], mean=0.5, stddev=0.0))
        bn += tf.get_variable("b_noreg", initializer=tf.random_normal([1], mean=0.0, stddev=0.0))
        # bn *= tf.Variable(0.5, name=(scope.name + "/a_noreg"))
        # bn += tf.Variable(0.0, name=(scope.name + "/b_noreg"))

    return bn

def relu_fc(input_2D_tensor_list, features_len, new_features_len, config):
    """make a relu fully-connected layer, mainly change the shape of tensor
       both input and output is a list of tensor
        argument:
            input_2D_tensor_list: list shape is [batch_size,feature_num]
            features_len: int the initial features length of input_2D_tensor
            new_feature_len: int the final features length of output_2D_tensor
            config: Config used for weights initializers
        return:
            output_2D_tensor_list lit shape is [batch_size,new_feature_len]
    """

    W = tf.get_variable(
        "relu_fc_weights",
        initializer=tf.random_normal(
            [features_len, new_features_len],
            mean=0.0,
            stddev=float(config.weights_stddev)
        )
    )
    b = tf.get_variable(
        "relu_fc_biases_noreg",
        initializer=tf.random_normal(
            [new_features_len],
            mean=float(config.bias_mean),
            stddev=float(config.weights_stddev)
        )
    )

    # intra-timestep multiplication:
    output_2D_tensor_list = [
        tf.nn.relu(tf.matmul(input_2D_tensor, W) + b)
            for input_2D_tensor in input_2D_tensor_list
    ]

    return output_2D_tensor_list

def single_LSTM_cell(input_hidden_tensor, n_outputs):
    with tf.variable_scope("lstm_cell"):
        lstm_cell = tf.contrib.rnn.BasicLSTMCell(n_outputs, state_is_tuple=True, forget_bias=0.999)
        #outputs, _ = tf.nn.rnn(lstm_cell, input_hidden_tensor, dtype=tf.float32)
        outputs, _ = tf.contrib.rnn.static_rnn(lstm_cell, input_hidden_tensor, dtype=tf.float32)
    res_output = []
    for i in xrange(len(outputs)): #relu_fc(input_hidden_tensor, n_input, n_output, config), n_output)[i]
        res_output.append(outputs[i]  )
    return res_output

def single_LSTM_Res_cell(input_hidden_tensor, n_outputs, res_unit):
    with tf.variable_scope("lstm_cell"):
        lstm_cell = tf.contrib.rnn.BasicLSTMCell(n_outputs, state_is_tuple=True, forget_bias=0.999)
        #outputs, _ = tf.nn.rnn(lstm_cell, input_hidden_tensor, dtype=tf.float32)
        outputs, _ = tf.contrib.rnn.static_rnn(lstm_cell, input_hidden_tensor, dtype=tf.float32)
    return [out+res_unit[i] for i, out in enumerate(outputs)]

def stack_single_LSTM_layer(input_hidden_tensor, n_input, n_output, layer_level, config, keep_prob_for_dropout):

    with tf.variable_scope('layer_{}'.format(layer_level)) as scope:
        if config.batch_norm_enabled :
            input_hidden_tensor = [apply_batch_norm(out, config, i) for i, out in enumerate(input_hidden_tensor)]
        hidden_LSTM_layer = single_LSTM_Res_cell(input_hidden_tensor, n_output, relu_fc(input_hidden_tensor, n_input, n_output, config))

        return hidden_LSTM_layer


def get_deeply_stacked_LSTM_layers(input_hidden_tensor, n_input, n_output, config, keep_prob_for_dropout):
    print "\nCreating hidden #1:"
    hidden = stack_single_LSTM_layer(input_hidden_tensor, config.n_inputs, config.n_hidden, 1, config, keep_prob_for_dropout)
    print (len(hidden), str(hidden[0].get_shape()))
    for stacked_hidden_index in range(config.n_stacked_layers - 1):
        print "\nCreating hidden #{}:".format(stacked_hidden_index + 2)
        hidden = stack_single_LSTM_layer(hidden, config.n_hidden, config.n_hidden, stacked_hidden_index + 2, config,
                                         keep_prob_for_dropout)
        print (len(hidden), str(hidden[0].get_shape()))
    print ""
    return hidden


def deep_LSTM_network(feature_mat, config, keep_prob_for_dropout):
    with tf.variable_scope('LSTM_network') as scope:  # TensorFlow graph naming

        feature_mat = tf.nn.dropout(feature_mat, keep_prob_for_dropout)
        feature_mat = tf.transpose(feature_mat, [1, 0, 2])
        feature_mat = tf.reshape(feature_mat, [-1, config.n_inputs])
        print feature_mat.get_shape()

        # Split the series because the rnn cell needs time_steps features, each of shape:
        hidden = tf.split(axis=0, num_or_size_splits=config.n_steps, value=feature_mat)
        print (len(hidden), str(hidden[0].get_shape()))
        # New shape: a list of lenght "time_step" containing tensors of shape [batch_size, n_hidden]

        hidden = get_deeply_stacked_LSTM_layers(hidden, config.n_inputs, config.n_hidden, config, keep_prob_for_dropout)

        # Final fully-connected activation logits
        # Get the last output tensor of the inner loop output series, of shape [batch_size, n_classes]
        lstm_last_output = hidden[-1]

        # Linear activation
        return tf.matmul(lstm_last_output, config.W['output']) + config.biases['output']

        last_hidden = tf.nn.dropout(hidden[-1], keep_prob_for_dropout)
        last_logits = relu_fc(
            [last_hidden],
            config.n_hidden, config.n_classes, config
        )[0]
        return last_logits

################################## load data and config ##################################
X_train, y_train, X_test, y_test, series_size = get_mnist_data_with_time_series_size()

class ResLstmConfig(Config):
    def __init__(self):
        super(ResLstmConfig, self).__init__()
        self.train_count = len(X_train)  # 7352 training series
        self.test_data_count = len(X_test)  # 2947 testing series
        self.n_steps = len(X_train[0])  # 128 time_steps per series

        # Trainging
        self.learning_rate = 0.005
        self.lambda_loss_amount = 0.0015
        self.training_epochs = 100
        self.batch_size = 1500

        # LSTM structure
        self.n_inputs = len(X_train[0][0])
        self.n_hidden = 28 # nb of neurons inside the neural network
        self.n_classes = len(y_train[0])  # Final output classes

        self.keep_prob_for_dropout = 0.85
        self.bias_mean = 0.3
        self.weights_stddev = 0.9
        self.n_layers_in_highway = 0
        self.n_stacked_layers = 4
        self.also_add_dropout_between_stacked_cells = False
        self.batch_norm_enabled = True

        self.model_name = "residual_lstm_" + "MNIST"
        self.log_folder_suffix = self.attach_log_suffix()
        self.logs_path = "/tmp/LSTM_logs/"+self.log_folder_suffix

        self.tensor_board_logging_enabled = True
        self.tensorboard_cmd = "tensorboard --logdir="+ self.logs_path
        self.model_desc_attched_string = self.attach_mdoel_desc()
        self.matplot_lib_enabled = True
        self.matplot_lib_for_accuracy =True
        self.matplot_lib_for_single_ybundle=False


#config = Config(X_train, X_test)
config = ResLstmConfig()



def run_with_config(config) : #, X_train, y_train, X_test, y_test):
    tf.reset_default_graph()  # To enable to run multiple things in a loop
    config.print_config()

    if config.matplot_lib_enabled:
        # To keep track of training's performance
        test_losses = []
        test_accuracies = []
        indep_test_axis = []



    config.W = {
        'hidden': tf.Variable(tf.random_normal([config.n_inputs, config.n_hidden])),
        'output': tf.Variable(tf.random_normal([config.n_hidden, config.n_classes]))
    }
    config.biases = {
        'hidden': tf.Variable(tf.random_normal([config.n_hidden], mean=1.0)),
        'output': tf.Variable(tf.random_normal([config.n_classes]))
    }
    #-----------------------------------
    # Define parameters for model
    #-----------------------------------


    print("Some useful info to get an insight on dataset's shape and normalisation:")
    print("features shape, labels shape, each features mean, each features standard deviation")
    print(X_test.shape, y_test.shape,
          np.mean(X_test), np.std(X_test))
    print("the dataset is therefore properly normalised, as expected.")

    # ------------------------------------------------------
    # step3: Let's get serious and build the neural network
    # ------------------------------------------------------
    X = tf.placeholder(tf.float32, [None, config.n_steps, config.n_inputs])
    Y = tf.placeholder(tf.float32, [None, config.n_classes])

    pred_Y = deep_LSTM_network(X, config, 0.85)


    print "Unregularised variables:"
    for unreg in [tf_var.name for tf_var in tf.trainable_variables() if
                  ("noreg" in tf_var.name or "Bias" in tf_var.name)]:
        print unreg

    # Loss,optimizer,evaluation
    l2 = config.lambda_loss_amount * \
         sum(tf.nn.l2_loss(tf_var) for tf_var in tf.trainable_variables())
    # Softmax loss and L2
    cost = tf.reduce_mean(
        tf.nn.softmax_cross_entropy_with_logits(logits=pred_Y, labels=Y)) + l2
    optimizer = tf.train.AdamOptimizer(
        learning_rate=config.learning_rate).minimize(cost)

    correct_pred = tf.equal(tf.argmax(pred_Y, 1), tf.argmax(Y, 1))
    accuracy = tf.reduce_mean(tf.cast(correct_pred, dtype=tf.float32))
    # ------------------------------------------------------
    # step3.5 : Tensorboard stuff here
    # ------------------------------------------------------
    if config.tensor_board_logging_enabled:
        tf.summary.scalar("loss", cost)
        tf.summary.scalar("accuracy", accuracy)
        merged_summary_op = tf.summary.merge_all()

    # --------------------------------------------
    # step4: Hooray, now train the neural network
    # --------------------------------------------
    # Note that log_device_placement can be turned ON but will cause console spam.
    sess = tf.InteractiveSession(config=tf.ConfigProto(log_device_placement=False))
    tf.global_variables_initializer().run()

    if config.tensor_board_logging_enabled:
        # op to write logs to Tensorboard
        summary_writer = tf.summary.FileWriter(config.logs_path, graph=tf.get_default_graph())

    best_accuracy = 0.0
    # Start training for each batch and loop epochs
    for i in range(config.training_epochs):
        for start, end in zip(range(0, config.train_count, config.batch_size),
                              range(config.batch_size, config.train_count + 1, config.batch_size)):
            if config.tensor_board_logging_enabled:
                _, summary = sess.run([optimizer, merged_summary_op],
                                      feed_dict={X: X_train[start:end], Y: y_train[start:end]})
            else:
                sess.run(optimizer, feed_dict={X: X_train[start:end], Y: y_train[start:end]})

        if config.tensor_board_logging_enabled:
            # Write logs at every iteration
            summary_writer.add_summary(summary, i)

        # Test completely at every epoch: calculate accuracy
        pred_out, accuracy_out, loss_out = sess.run([pred_Y, accuracy, cost], feed_dict={
            X: X_test, Y: y_test})

        if config.matplot_lib_enabled:
            indep_test_axis.append(i)
            test_losses.append(loss_out)
            test_accuracies.append(accuracy_out)

        print("traing iter: {},".format(i) + \
              " test accuracy : {},".format(accuracy_out) + \
              " loss : {}".format(loss_out))
        best_accuracy = max(best_accuracy, accuracy_out)

    print("")
    print("final test accuracy: {}".format(accuracy_out))
    print("best epoch's test accuracy: {}".format(best_accuracy))
    print("")

    if config.tensor_board_logging_enabled:
        print("Run the command line:\n")
        print(config.tensorboard_cmd)
        print("\nThen open http://0.0.0.0:6006/ into your web browser")

    print(config.model_desc_attched_string)


    if config.matplot_lib_enabled:

        #for i in range(config.batch_size):
         #   indep_test_axis.append(i)
        #indep_test_axis = [i for i in range(config.batch_size)]
        #indep_test_axis = np.array(indep_test_axis)

        #p = PlotUtil("title", indep_test_axis, "x_label", "y_label")
        y_bundle = []
        test_losses = np.array(test_losses)
        test_accuracies = np.array(test_accuracies)

        y = YaxisBundle(np.array(test_losses), "loss", "b")
        y_bundle.append(y)

        y = YaxisBundle(np.array(test_accuracies), "accuracy", "g")
        y_bundle.append(y)

        #p.show_plot(y_bundle)

        if config.matplot_lib_for_single_ybundle:
            if config.matplot_lib_for_accuracy:
                return y_bundle[1]
            else :
                return y_bundle[0]
        return y_bundle



if __name__ == '__main__':
    if config.matplot_lib_enabled:
        indep_test_axis = []
        for i in range(config.training_epochs):
            indep_test_axis.append(i)

        p = PlotUtil("Residual LSTM(3 layers) on MNIST", np.array(indep_test_axis), "Epoch iterations", "Loss or Accuracy")
        y_bundle = run_with_config(config)

        p.show_plot(y_bundle)
    else:
        run_with_config(config)

